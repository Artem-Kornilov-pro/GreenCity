"""Защита тяжёлых эндпоинтов (api/protection.py, core/concurrency.py) и
закрытый CORS (main.py): лимит частоты по IP, лимит размера тела, 503 при
переполненной очереди расчётов."""

import glob

import pytest
from fastapi.testclient import TestClient

from api import editor as editor_api
from api import greenplan as greenplan_api
from api import protection
from core.concurrency import ServerBusy
from core.paths import LOCATIONS_DIR
from main import app

SMALL_DXF = glob.glob(str(LOCATIONS_DIR / "location_old" / "01_*" / "*.dxf"))[0]


@pytest.fixture
def client(fake_mongo, fake_redis):
    with TestClient(app) as c:
        yield c


# --- Лимит частоты ------------------------------------------------------------


def test_rate_limit_answers_429_with_retry_after(client, monkeypatch):
    monkeypatch.setitem(protection.RATE_LIMITS, "llm", 2)
    # Пустой список решений: текст не нужен, модель не вызывается.
    assert client.post("/api/greenplan/report", json=[]).status_code == 200
    assert client.post("/api/greenplan/report", json=[]).status_code == 200
    r = client.post("/api/greenplan/report", json=[])
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) == protection.WINDOW_S
    assert "Слишком много запросов" in r.json()["detail"]


def test_rate_limit_is_per_client_ip(client, monkeypatch):
    monkeypatch.setitem(protection.RATE_LIMITS, "llm", 1)
    assert client.post("/api/greenplan/report", json=[]).status_code == 200
    assert client.post("/api/greenplan/report", json=[]).status_code == 429
    with TestClient(app, client=("10.0.0.2", 50000)) as other:
        assert other.post("/api/greenplan/report", json=[]).status_code == 200


def test_rate_limit_groups_are_independent(client, monkeypatch):
    monkeypatch.setitem(protection.RATE_LIMITS, "llm", 1)
    client.post("/api/greenplan/report", json=[])
    assert client.post("/api/greenplan/report", json=[]).status_code == 429
    assert client.post("/api/export-dxf", json=_scene(client)).status_code == 200


def test_rate_limit_multiplier_zero_disables_limits(client, monkeypatch):
    monkeypatch.setitem(protection.RATE_LIMITS, "llm", 1)
    monkeypatch.setattr(protection, "RATE_LIMIT_MULTIPLIER", 0)
    for _ in range(3):
        assert client.post("/api/greenplan/report", json=[]).status_code == 200


def test_light_endpoints_are_not_rate_limited(client, monkeypatch):
    for group in protection.RATE_LIMITS:
        monkeypatch.setitem(protection.RATE_LIMITS, group, 1)
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/catalog").status_code == 200


# --- Размер тела ----------------------------------------------------------------


def test_body_over_limit_is_rejected_with_413(client, monkeypatch):
    monkeypatch.setattr(protection, "MAX_BODY_MB", 0.001)
    r = client.post("/api/greenplan/report", json=[{"zone_id": "z" * 2000}])
    assert r.status_code == 413
    assert "МБ" in r.json()["detail"]


def test_body_without_content_length_is_counted_while_reading(client, monkeypatch):
    monkeypatch.setattr(protection, "MAX_BODY_MB", 0.001)

    def chunks():
        yield b"["
        for _ in range(100):
            yield b'{"zone_id": "' + b"z" * 100 + b'"},'
        yield b"{}]"

    r = client.post("/api/greenplan/report", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_upload_has_its_own_larger_limit(client, monkeypatch):
    monkeypatch.setattr(protection, "MAX_BODY_MB", 0.001)
    with open(SMALL_DXF, "rb") as f:
        assert client.post("/api/parse", files={"file": ("site.dxf", f, "application/dxf")}).status_code == 200
    monkeypatch.setattr(protection, "MAX_UPLOAD_MB", 0.001)
    with open(SMALL_DXF, "rb") as f:
        assert client.post("/api/parse", files={"file": ("site.dxf", f, "application/dxf")}).status_code == 413


def test_too_many_dwg_files_are_rejected(client, monkeypatch):
    monkeypatch.setattr(editor_api, "MAX_DWG_FILES", 1)
    files = [("files", ("a.dwg", b"dwg", "application/octet-stream")), ("files", ("b.dwg", b"dwg", "application/octet-stream"))]
    r = client.post("/api/parse-dwg", files=files)
    assert r.status_code == 400
    assert "Не больше 1" in r.json()["detail"]


# --- Очередь расчётов -----------------------------------------------------------


def test_busy_server_answers_503_with_retry_after(client, monkeypatch):
    async def _busy(*args, **kwargs):
        raise ServerBusy("Сервер занят расчётами других пользователей.", retry_after_s=45)

    scene = _scene(client)
    monkeypatch.setattr(greenplan_api, "run_heavy", _busy)
    r = client.post("/api/greenplan/generate", json={"scene": scene})
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "45"
    assert r.json()["detail"].startswith("Сервер занят")


def test_busy_server_is_not_hidden_by_export_fallback(client, monkeypatch):
    # Сбой наложения на исходник даёт сборку из сцены, но «сервер занят» --
    # не сбой наложения: его нельзя подменять вторым ожиданием очереди.
    scene = _scene(client)

    async def _busy(*args, **kwargs):
        raise ServerBusy("Сервер занят.")

    monkeypatch.setattr(editor_api, "run_heavy", _busy)
    assert client.post("/api/export-dxf", json=scene).status_code == 503


# --- CORS -------------------------------------------------------------------------


def test_cors_is_closed_by_default(client):
    r = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    preflight = client.options(
        "/api/greenplan/generate",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def _scene(client) -> dict:
    with open(SMALL_DXF, "rb") as f:
        r = client.post("/api/parse", files={"file": ("site.dxf", f, "application/dxf")})
    assert r.status_code == 200, r.text
    return r.json()
