"""Сквозной сценарий ручного редактора: загрузить DXF -> правка текстом
(облачная LLM подменена фиксированным планом) -> сетка по кнопке генерации ->
экспорт DXF -> повторная загрузка. Модель задаёт только намерение; то, что
координаты посчитаны по нормам (включая породные отступы -- липе 10 м от
здания), проверяется независимым отчётом о нарушениях."""

import json

from core.schemas import Scene
from greenplan.violation_report import find_violations

COURTYARD = "02_courtyard_3buildings"  # три здания, дорожки, сети


def _plants(scene: dict) -> list[dict]:
    return [o for o in scene["objects"] if o["type"] in ("tree", "bush")]


def test_text_edit_then_export_round_trip(client, e2e, fake_text_editor_llm):
    scene = e2e.upload_dxf(client, e2e.location_dxf(COURTYARD))
    fake_text_editor_llm.content = json.dumps(
        {
            "operations": [
                {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["species_spireya_seraya"], "max_count": 12},
                {"op": "place_in_area", "catalog_ids": ["species_lipa_melkolistnaya"], "count": 4},
                {"op": "add", "catalog_id": "bench", "x": 0, "z": 0},
                {"op": "no_such_operation"},
            ],
            "explanation": "Изгородь вдоль дорожек, четыре липы, лавка.",
        }
    )

    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": "обсади дорожки и посади липы"})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["explanation"] == "Изгородь вдоль дорожек, четыре липы, лавка."
    assert len(result["applied"]) >= 3
    assert any("no_such_operation" in reason for reason in result["rejected"])

    # Модели ушли инструкция и контекст участка (каталог, контуры).
    prompt = fake_text_editor_llm.prompt_text()
    assert "обсади дорожки и посади липы" in prompt
    assert "catalog_rows" in prompt

    edited = result["scene"]
    created = [o for o in edited["objects"] if o["metadata"].get("source") == "llm"]
    lindens = [o for o in created if o["metadata"].get("species") == "Липа мелколистная"]
    assert lindens and any(o["metadata"].get("species") == "Спирея серая" for o in created)

    # Независимая проверка: ни одна посадка от правки текстом не нарушает
    # норм, в том числе 10 м от здания для липы (743-ПП).
    created_ids = {o["id"] for o in created}
    violations = [v for v in find_violations(Scene.model_validate(edited)) if v.object_id in created_ids]
    assert violations == []

    # Генерация по сетке поверх правок -- не трогает уже стоящие объекты.
    r = client.post("/api/generate-greenery", json=edited)
    assert r.status_code == 200
    generated_scene = r.json()
    ids_after = {o["id"] for o in generated_scene["objects"]}
    assert {o["id"] for o in edited["objects"]} <= ids_after

    # Экспорт и повторная загрузка: посадок столько же, сколько было в сцене.
    r = client.post("/api/export-dxf", json=generated_scene)
    assert r.status_code == 200
    reparsed = client.post("/api/parse", files={"file": ("plan.dxf", r.content, "application/dxf")}).json()
    assert len(_plants(reparsed)) == len(_plants(generated_scene))


def test_text_edit_without_llm_keys_is_a_clear_503(client, e2e):
    # Ключи не заданы (tests/conftest.py чистит окружение) -- сервис жив,
    # отвечает понятной ошибкой, а не 500.
    scene = e2e.upload_dxf(client, e2e.location_dxf(COURTYARD))
    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": "посади дерево"})
    assert r.status_code == 503
    assert r.json()["detail"]
