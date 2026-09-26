"""Сквозной сценарий GreenPlan, как его проходит пользователь во фронтенде:
загрузить DXF -> GreenPlan -> текст-обоснование -> пояснительная записка ->
экспорт итогового DXF -> повторная загрузка экспортированного файла.

Проверяется не "эндпоинт ответил 200", а сквозные свойства результата:
новые посадки не нарушают норм, виды из ассортимента и не инвазивные,
ведомость в записке совпадает с тем, что показал GreenPlan, новая посадка
переживает экспорт и повторный разбор на отдельных слоях."""

import httpx
import openai
import pytest

from core.invasive_species import invasive_match
from greenplan import decision_report

# Разные по устройству участки: реальная улица с волнистыми рядами, реальный
# проезд с рощами, синтетическое кольцо вокруг здания.
PROJECTS = ["10_stary_gay", "12_natashinsky_proezd", "25_classical_building_ring_primer"]


@pytest.mark.parametrize("slug", PROJECTS)
def test_dxf_to_greenplan_to_document_to_dxf(client, e2e, fake_report_llm, slug):
    scene = e2e.upload_dxf(client, e2e.location_dxf(slug))
    existing_ids = {o["id"] for o in scene["objects"]}

    # 1. GreenPlan
    r = client.post("/api/greenplan/generate", json={"scene": scene})
    assert r.status_code == 200, r.text
    result = r.json()
    planned = result["scene"]
    new_objects = [o for o in planned["objects"] if o["id"] not in existing_ids]
    assert new_objects, "GreenPlan должен что-то посадить на реальном участке"
    assert all(o["metadata"]["generated"] for o in new_objects)

    # Каждая посадка -- из видов, подобранных для её зоны, и не инвазивная.
    by_zone = {a["zone_id"]: a for a in result["assignments"]}
    for obj in new_objects:
        assignment = by_zone[obj["metadata"]["zone_id"]]
        assert obj["metadata"]["species"] in assignment["tree_species"] + assignment["bush_species"]
        assert invasive_match(obj["metadata"]["species"]) is None
    assert all(a["species_basis"] for a in result["assignments"])

    # Независимая проверка норм на сервере: у новых посадок нарушений нет.
    new_ids = {o["id"] for o in new_objects}
    assert [v for v in result["violations"] if v["object_id"] in new_ids] == []

    # Ведомость -- ровно по новым посадкам (в штуках) плюс новый газон (в м²).
    pieces = [row for row in result["assortment"] if row["unit"] == "шт."]
    assert sum(row["count"] for row in pieces) == len(new_objects)
    new_lawns = [a for a in planned["lawns"] if a["status"] == "new"]
    assert new_lawns, "на участке есть открытая земля -- должен быть новый газон"
    lawn_rows = [row for row in result["assortment"] if row["unit"] == "м²"]
    assert [row["species"] for row in lawn_rows] == ["Газон обыкновенный"]
    assert lawn_rows[0]["count"] == round(sum(a["area_sqm"] for a in new_lawns))

    # 2. Текст-обоснование (локальная LLM подменена): в промпт ушли
    # настоящие решения, включая подобранные виды.
    r = client.post("/api/greenplan/report", json=result["assignments"])
    assert r.status_code == 200
    report = r.json()
    assert report["report_error"] is None and report["report"]
    prompt = fake_report_llm.prompt_text()
    some_species = (result["assignments"][0]["tree_species"] + result["assignments"][0]["bush_species"])[0]
    assert some_species in prompt

    # 3. Пояснительная записка по той же сцене и решениям.
    r = client.post(
        "/api/greenplan/document",
        json={"scene": planned, "assignments": result["assignments"], "report": report["report"], "title": slug},
    )
    assert r.status_code == 200
    text = e2e.docx_text(r.content)
    assert "Нарушений у новых посадок: 0" in text
    for row in pieces:
        assert f"| {row['species']} | — | {row['count']} |" in text
    lawn_m2 = f"{lawn_rows[0]['count']:,}".replace(",", " ")
    assert f"| Газон обыкновенный | — | {lawn_m2} м² |" in text
    assert "Приложение А. Обоснование решений (текст ИИ — проверить)" in text
    assert report["report"] in text

    # 4. Экспорт DXF и повторный разбор: новая посадка -- на слоях NEW_* и
    # не теряется, существующее -- на своих слоях.
    r = client.post("/api/export-dxf", json=planned)
    assert r.status_code == 200
    reparsed = client.post("/api/parse", files={"file": ("export.dxf", r.content, "application/dxf")}).json()
    new_layer_objects = [o for o in reparsed["objects"] if str(o["metadata"].get("sourceLayer", "")).startswith("NEW_")]
    assert len(new_layer_objects) == len(new_objects)
    assert reparsed["boundary"] is not None
    # Новый газон -- заливкой на слое NEW_LAWN, читается обратно как зона газона.
    assert any(z["severity"] == "allowed" and "NEW_LAWN" in z["name"] for z in reparsed["restrictions"])


def test_report_unavailable_does_not_block_the_document(client, e2e, monkeypatch):
    # Ollama недоступна (подменена ошибкой соединения, а не "не запущена":
    # на машине разработчика она может и работать) -- текст не приходит, но
    # записка формируется: текст ИИ -- только приложение.
    def _unreachable():
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))

    monkeypatch.setattr(decision_report, "_client", _unreachable)
    scene = e2e.upload_dxf(client, e2e.location_dxf("25_classical_building_ring_primer"))
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()

    report = client.post("/api/greenplan/report", json=result["assignments"]).json()
    assert report["report"] is None and report["report_error"]

    r = client.post("/api/greenplan/document", json={"scene": result["scene"], "assignments": result["assignments"]})
    assert r.status_code == 200
    text = e2e.docx_text(r.content)
    assert "3. Принятые решения" in text
    assert "Приложение А" not in text


def test_greenplan_is_deterministic_across_requests(client, e2e):
    scene = e2e.upload_dxf(client, e2e.location_dxf("12_natashinsky_proezd"))
    first = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    second = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    assert first["scene"]["objects"] == second["scene"]["objects"]
    assert first["assignments"] == second["assignments"]


def test_greenplan_with_user_options(client, e2e):
    # Параметры из диалога GreenPlan: стиль, предпочтительный вид,
    # благоустройство -- через HTTP, записку и DXF туда и обратно.
    scene = e2e.upload_dxf(client, e2e.location_dxf("10_stary_gay"))
    options = {
        "style": "landscape", "lawn": False, "preferred_trees": ["species_lipa_melkolistnaya"],
        "paths": True, "lighting": True, "benches": True,
    }
    result = client.post("/api/greenplan/generate", json={"scene": scene, "options": options}).json()
    planned = result["scene"]
    assert {a["site_style"] for a in result["assignments"]} == {"landscape"}
    assert planned["lawns"] == []
    trees = [o for o in planned["objects"] if o["type"] == "tree" and o["metadata"].get("source") == "greenplan"]
    assert any(t["metadata"]["species"] == "Липа мелколистная" for t in trees)
    rows = {row["species"]: row for row in result["improvements"]}
    assert rows["Дорожка (новая)"]["count"] > 0 and rows["Фонарь"]["count"] > 0
    new_ids = {o["id"] for o in planned["objects"] if o["metadata"].get("source") == "greenplan"}
    assert [v for v in result["violations"] if v["object_id"] in new_ids] == []

    r = client.post("/api/greenplan/document", json={
        "scene": planned, "assignments": result["assignments"], "title": "двор",
        "options": options, "notes": result["notes"],
    })
    text = e2e.docx_text(r.content)
    assert "Параметры, заданные пользователем" in text and "Стиль участка: пейзажный." in text
    assert "4.1. Благоустройство" in text and "Газон обыкновенный" not in text

    exported = client.post("/api/export-dxf", json=planned).content
    reparsed = client.post("/api/parse", files={"file": ("export.dxf", exported, "application/dxf")}).json()
    assert any(z["type"] == "pedestrian_path" and "NEW_PATHS" in z["name"] for z in reparsed["restrictions"])
    new_lamps = [o for o in reparsed["objects"] if o["type"] == "lamp" and str(o["metadata"].get("sourceLayer", "")).startswith("NEW_")]
    assert len(new_lamps) == rows["Фонарь"]["count"]
