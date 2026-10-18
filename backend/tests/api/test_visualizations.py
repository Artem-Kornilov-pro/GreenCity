"""Визуализации плана YandexART (issue #114) -- без сети: генерация подменена."""

import base64

import pytest
from fastapi.testclient import TestClient

from core import yandex_ai
from greenplan import visualization
from main import app

SITE = "locations/location_test/01_school/01_school.dxf"


@pytest.fixture
def client(fake_mongo, fake_redis, tmp_path, monkeypatch):
    monkeypatch.setenv("GREENCITY_SOURCES_DIR", str(tmp_path / "sources"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def planted(client):
    from core.paths import REPO_ROOT

    scene = client.post("/api/parse", files={"file": ("site.dxf", (REPO_ROOT / SITE).read_bytes(), "application/dxf")}).json()
    return client.post("/api/greenplan/generate", json={"scene": scene}).json()


def test_prompt_describes_the_plan(planted):
    from core.schemas import Scene
    from greenplan.pattern_assignment import ZoneAssignment

    scene = Scene.model_validate(planted["scene"])
    assignments = [ZoneAssignment.model_validate(a) for a in planted["assignments"]]
    text = visualization.plan_description(scene, assignments)
    species = {o.metadata.get("species") for o in scene.objects if o.metadata.get("generated") and o.type == "tree"}
    assert text.startswith("Городской участок в Москве")
    assert "деревья:" in text and any(s.lower() in text for s in species if s)
    assert "в пейзажном стиле" in text or "в регулярном стиле" in text
    prompt = visualization.prompt_for("pedestrian", text)
    assert "уровне глаз пешехода" in prompt and "без текста" in prompt
    assert len(prompt) <= visualization.MAX_PROMPT_CHARS


def test_long_description_is_cut_by_whole_parts():
    text = "Городской участок в Москве; деревья: " + ", ".join(["липа мелколистная"] * 20) + "; кустарники: спирея."
    prompt = visualization.prompt_for("overview", text)
    assert len(prompt) <= visualization.MAX_PROMPT_CHARS
    assert "Городской участок в Москве." in prompt and "кустарники" not in prompt and prompt.endswith("без текста и надписей.")


def test_visualizations_come_back_as_jpeg_with_a_note(client, planted, monkeypatch):
    monkeypatch.setattr(yandex_ai, "art_credentials", lambda: ("key", "folder", "yandex-art/latest"))
    seen = []

    async def _fake(client_, api_key, folder, prompt, seed, model):
        seen.append((folder, seed))
        return b"\xff\xd8jpeg"

    monkeypatch.setattr(visualization, "_generate", _fake)
    body = {"scene": planted["scene"], "assignments": planted["assignments"], "views": ["overview", "detail"], "seed": 7}
    r = client.post("/api/greenplan/visualizations", json=body)
    assert r.status_code == 200
    data = r.json()
    assert "Иллюстрация ИИ" in data["note"]
    assert [v["view"] for v in data["visualizations"]] == ["overview", "detail"]
    assert base64.b64decode(data["visualizations"][0]["image"]) == b"\xff\xd8jpeg"
    assert seen == [("folder", 7), ("folder", 8)]


def test_not_configured_is_503(client, planted, monkeypatch):
    monkeypatch.setattr(yandex_ai, "art_credentials", lambda: None)
    r = client.post("/api/greenplan/visualizations", json={"scene": planted["scene"], "assignments": planted["assignments"]})
    assert r.status_code == 503 and "YANDEX_CLOUD_API_KEY" in r.json()["detail"]


def test_model_failure_is_502(client, planted, monkeypatch):
    monkeypatch.setattr(yandex_ai, "art_credentials", lambda: ("key", "folder", "yandex-art/latest"))

    async def _fail(*args):
        raise visualization.VisualizationFailed("YandexART отказал (429)")

    monkeypatch.setattr(visualization, "_generate", _fail)
    r = client.post("/api/greenplan/visualizations", json={"scene": planted["scene"], "assignments": planted["assignments"]})
    assert r.status_code == 502 and "429" in r.json()["detail"]


def test_unknown_view_is_refused(client, planted):
    body = {"scene": planted["scene"], "assignments": planted["assignments"], "views": ["aerial"]}
    assert client.post("/api/greenplan/visualizations", json=body).status_code == 422


def test_access_denied_explains_which_role_to_grant(monkeypatch):
    import asyncio

    import httpx

    def _handler(request):
        return httpx.Response(403, json={"error": "Access to model denied"})

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            await visualization._generate(client, "key", "folder", "prompt", 1, "yandex-art/latest")

    with pytest.raises(visualization.VisualizationFailed, match="ai.imageGeneration.user"):
        asyncio.run(_run())


def test_operation_is_polled_until_the_image_is_ready(monkeypatch):
    import asyncio

    import httpx

    monkeypatch.setattr(visualization, "POLL_INTERVAL_S", 0)
    polls = []

    def _handler(request):
        if request.method == "POST":
            assert request.url == visualization.ART_URL
            assert request.read().decode().count("art://folder/yandex-art/latest") == 1
            return httpx.Response(200, json={"id": "op1", "done": False})
        polls.append(1)
        if len(polls) < 2:
            return httpx.Response(200, json={"id": "op1", "done": False})
        return httpx.Response(200, json={"id": "op1", "done": True, "response": {"image": base64.b64encode(b"JPG").decode()}})

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await visualization._generate(client, "key", "folder", "prompt", 1, "yandex-art/latest")

    assert asyncio.run(_run()) == b"JPG" and len(polls) == 2


def test_art_key_is_separate_from_the_llm_key(monkeypatch):
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "llm-key")
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "llm-folder")
    for var in ("YANDEX_ART_API_KEY", "YANDEX_ART_FOLDER", "YANDEX_ART_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert yandex_ai.art_credentials() == ("llm-key", "llm-folder", "aliceai-image-art-3.0")
    monkeypatch.setenv("YANDEX_ART_API_KEY", "art-key")
    monkeypatch.setenv("YANDEX_ART_FOLDER", "art-folder")
    monkeypatch.setenv("YANDEX_ART_MODEL", "yandex-art-2.0/latest")
    assert yandex_ai.art_credentials() == ("art-key", "art-folder", "yandex-art-2.0/latest")
    assert yandex_ai.credentials() == ("llm-key", "llm-folder")


def _openai_run(handler, model="aliceai-image-art-3.0"):
    import asyncio

    import httpx

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await visualization._generate(client, "key", "folder", "prompt", 1, model)

    return asyncio.run(_run())


def test_alice_art_goes_through_the_openai_compatible_api():
    import json

    import httpx

    def _handler(request):
        assert request.url == visualization.IMAGES_URL
        assert request.headers["Authorization"] == "Bearer key" and request.headers["OpenAI-Project"] == "folder"
        assert json.loads(request.read())["model"] == "art://folder/aliceai-image-art-3.0"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"JPG").decode()}]})

    assert _openai_run(_handler) == b"JPG"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (403, {"error": "denied"}, "ai.imageGeneration.user"),
        (500, {"error": {"message": "failed to execute calls to all endpoints"}}, "нет доступа к модели"),
        (200, {"data": []}, "без изображения"),
    ],
)
def test_openai_compatible_api_failures_are_explained(status, body, expected):
    import httpx

    with pytest.raises(visualization.VisualizationFailed, match=expected):
        _openai_run(lambda request: httpx.Response(status, json=body))
