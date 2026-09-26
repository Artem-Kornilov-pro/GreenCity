"""main.py -- сквозные тесты через TestClient (реальный HTTP-стек FastAPI,
без реальной сети): MongoDB/Redis подменены fake_mongo/fake_redis (conftest.py),
LLM НИКОГДА не вызывается по-настоящему -- main.edit_scene_with_text
подменяется прямо в модуле main (то же имя, что там импортировано)."""

import glob
import io

import ezdxf
import pytest
from fastapi.testclient import TestClient

from api import accounts as accounts_api
from api import editor as editor_api
from api import greenplan as greenplan_api
from core.paths import LOCATIONS_DIR
from exchange import dwg_batch_converter
from main import app
from text_editor.service import LlmError, LlmNotConfiguredError, TextEditResult

# locations/location_old/ -- см. комментарий у _location_path в conftest.py.
ROOT_LOCATIONS = glob.glob(
    str(LOCATIONS_DIR / "location_old" / "01_*" / "*.dxf")
)


@pytest.fixture
def client(fake_mongo, fake_redis):
    with TestClient(app) as c:
        yield c


def _dxf_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _register(client, username="alice", password="password123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- /api/health / /metrics --------------------------------------------------


def test_health_endpoint(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_endpoint_exposes_prometheus_format(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text


# --- /api/catalog --------------------------------------------------------


def test_catalog_endpoint_returns_items(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200
    items = r.json()
    assert any(i["id"] == "bench" for i in items)


def test_catalog_endpoint_is_cached_on_second_call(client):
    first = client.get("/api/catalog")
    second = client.get("/api/catalog")
    assert first.json() == second.json()


# --- /api/parse ------------------------------------------------------------


def test_parse_valid_dxf_returns_scene(client):
    with open(ROOT_LOCATIONS[0], "rb") as f:
        r = client.post("/api/parse", files={"file": ("test.dxf", f, "application/dxf")})
    assert r.status_code == 200
    body = r.json()
    assert body["boundary"] is not None
    assert len(body["objects"]) > 0


def test_parse_rejects_non_dxf_extension(client):
    r = client.post("/api/parse", files={"file": ("test.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "dxf" in r.json()["detail"].lower()


def test_parse_rejects_corrupt_dxf_content(client):
    r = client.post("/api/parse", files={"file": ("test.dxf", b"not a real dxf file", "application/dxf")})
    assert r.status_code == 400
    assert "разобрать" in r.json()["detail"]


# --- /api/parse-dwg (issue #50) ---------------------------------------------
# dwg_batch_converter.merge_dwg_files подменяется целиком -- реального
# dwg2dxf/LibreDWG на тестовой машине может не быть, а сама эта функция уже
# покрыта отдельными юнит-тестами (test_dwg_batch_converter.py). Здесь --
# только контракт эндпоинта: коды ответов, dwgConversionWarnings, метрики.
# Разбор идёт в том же процессе (DWG_JOB_ISOLATED=0): подмена
# merge_dwg_files в дочерний процесс не переходит. Сам запуск в отдельном
# процессе проверяется в tests/exchange/test_dwg_job.py.


@pytest.fixture(autouse=True)
def _dwg_job_in_process(monkeypatch, tmp_path):
    monkeypatch.setenv("DWG_JOB_ISOLATED", "0")
    monkeypatch.setenv("DWG_SLOT_DIR", str(tmp_path / "dwg-slots"))


def test_parse_dwg_rejects_upload_without_any_dwg_file(client):
    r = client.post("/api/parse-dwg", files=[("files", ("test.txt", b"hello", "text/plain"))])
    assert r.status_code == 400
    assert "dwg" in r.json()["detail"].lower()


def test_parse_dwg_returns_503_when_tool_missing(client, monkeypatch):
    def _raise(dwg_paths, intermediate_dir, workers=1):
        raise dwg_batch_converter.Dwg2DxfNotFound("dwg2dxf не найден")

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _raise)
    r = client.post("/api/parse-dwg", files=[("files", ("a.dwg", b"whatever", "application/octet-stream"))])
    assert r.status_code == 503


def test_parse_dwg_returns_400_when_every_file_fails(client, monkeypatch):
    def _all_failed(dwg_paths, intermediate_dir, workers=1):
        import ezdxf

        result = dwg_batch_converter.BatchConversionResult(doc=ezdxf.new())
        result.failed = {p.name: "не удалось прочитать" for p in dwg_paths}
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _all_failed)
    r = client.post("/api/parse-dwg", files=[("files", ("a.dwg", b"whatever", "application/octet-stream"))])
    assert r.status_code == 400
    assert "a.dwg" in r.json()["detail"]


def test_parse_dwg_success_returns_scene_with_warnings_for_failed_files(client, monkeypatch):
    fake_scene = {"boundary": None, "restrictions": [], "objects": [], "windows": [], "canopies": [], "curbs": [],
                  "meta": {"scale": 1.0, "insunits": 6, "origin": {"x": 0, "y": 0}, "buildingCount": 0, "pointObjectCount": 0}}

    def _partial_success(dwg_paths, intermediate_dir, workers=1):
        import ezdxf

        result = dwg_batch_converter.BatchConversionResult(doc=ezdxf.new())
        result.converted = ["good.dwg"]
        result.failed = {"bad.dwg": "unsupported object type"}
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _partial_success)
    monkeypatch.setattr("exchange.dxf_parser.parse_dxf_doc", lambda doc: dict(fake_scene))

    r = client.post(
        "/api/parse-dwg",
        files=[
            ("files", ("good.dwg", b"whatever", "application/octet-stream")),
            ("files", ("bad.dwg", b"whatever", "application/octet-stream")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dwgConversionWarnings"] == [{"file": "bad.dwg", "error": "unsupported object type"}]


def test_parse_dwg_success_without_failures_has_no_warnings_key(client, monkeypatch):
    fake_scene = {"boundary": None, "restrictions": [], "objects": [], "windows": [], "canopies": [], "curbs": [],
                  "meta": {"scale": 1.0, "insunits": 6, "origin": {"x": 0, "y": 0}, "buildingCount": 0, "pointObjectCount": 0}}

    def _full_success(dwg_paths, intermediate_dir, workers=1):
        import ezdxf

        result = dwg_batch_converter.BatchConversionResult(doc=ezdxf.new())
        result.converted = [p.name for p in dwg_paths]
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _full_success)
    monkeypatch.setattr("exchange.dxf_parser.parse_dxf_doc", lambda doc: dict(fake_scene))

    r = client.post("/api/parse-dwg", files=[("files", ("good.dwg", b"whatever", "application/octet-stream"))])
    assert r.status_code == 200
    assert "dwgConversionWarnings" not in r.json()


# --- /api/generate-greenery --------------------------------------------------


def _parsed_scene(client):
    with open(ROOT_LOCATIONS[0], "rb") as f:
        r = client.post("/api/parse", files={"file": ("test.dxf", f, "application/dxf")})
    return r.json()


def test_generate_greenery_default_adds_trees_bushes_and_lawn(client):
    scene = _parsed_scene(client)
    r = client.post("/api/generate-greenery", json=scene)
    assert r.status_code == 200
    body = r.json()
    types = {o["type"] for o in body["objects"]}
    assert {"tree", "bush", "lawn_patch"} <= types


def test_generate_greenery_can_disable_individual_categories(client):
    scene = _parsed_scene(client)
    r = client.post("/api/generate-greenery?include_trees=false&include_lawn=false", json=scene)
    body = r.json()
    types = [o["type"] for o in body["objects"]]
    assert "bush" in types
    assert types.count("tree") == sum(1 for o in scene["objects"] if o["type"] == "tree")
    assert types.count("lawn_patch") == sum(1 for o in scene["objects"] if o["type"] == "lawn_patch")


def test_generate_greenery_rejects_grid_spacing_out_of_range(client):
    scene = _parsed_scene(client)
    r = client.post("/api/generate-greenery?grid_spacing_m=0.001", json=scene)
    assert r.status_code == 422


# --- /api/greenplan/generate (быстрый, без LLM вовсе) ------------------------


def test_greenplan_generate_returns_full_deterministic_result(client):
    scene = _parsed_scene(client)
    r = client.post("/api/greenplan/generate", json={"scene": scene})
    assert r.status_code == 200
    body = r.json()
    assert body["assignments"]
    # Ни report, ни report_error здесь больше нет вовсе -- текст отдельным
    # эндпоинтом (/api/greenplan/report), см. ниже.
    assert "report" not in body
    assert "report_error" not in body
    # violations/assortment -- всегда списки (возможно пустые), не отсутствуют
    assert isinstance(body["violations"], list)
    assert isinstance(body["assortment"], list)


def test_greenplan_generate_plans_lawn_and_lists_it_in_square_metres(client):
    body = client.post("/api/greenplan/generate", json={"scene": _parsed_scene(client)}).json()
    lawns = body["scene"]["lawns"]
    assert lawns and all(a["area_sqm"] > 0 for a in lawns)
    new_sqm = round(sum(a["area_sqm"] for a in lawns if a["status"] == "new"))
    lawn_rows = [row for row in body["assortment"] if row["unit"] == "м²"]
    assert lawn_rows == ([{"category": "газон", "species": "Газон обыкновенный", "count": new_sqm, "unit": "м²"}] if new_sqm else [])


def test_greenplan_generate_rejects_k_out_of_range(client):
    scene = _parsed_scene(client)
    r = client.post("/api/greenplan/generate?k=0", json={"scene": scene})
    assert r.status_code == 422


# --- /api/greenplan/report (LLM подменена, как и у edit-with-text) -----------


def test_greenplan_report_success(client, monkeypatch):
    scene = _parsed_scene(client)
    assignments = client.post("/api/greenplan/generate", json={"scene": scene}).json()["assignments"]

    monkeypatch.setattr(greenplan_api, "generate_report", lambda assignments: "связный текст отчёта")
    r = client.post("/api/greenplan/report", json=assignments)
    assert r.status_code == 200
    body = r.json()
    assert body["report"] == "связный текст отчёта"
    assert body["report_error"] is None


def test_greenplan_report_survives_llm_unavailable(client, monkeypatch):
    from greenplan.decision_report import DecisionReportUnavailable

    scene = _parsed_scene(client)
    assignments = client.post("/api/greenplan/generate", json={"scene": scene}).json()["assignments"]

    def _raise(assignments):
        raise DecisionReportUnavailable("YandexGPT недоступна")

    monkeypatch.setattr(greenplan_api, "generate_report", _raise)
    r = client.post("/api/greenplan/report", json=assignments)
    assert r.status_code == 200
    body = r.json()
    assert body["report"] is None
    assert body["report_error"] == "YandexGPT недоступна"


# --- /api/edit-with-text (LLM ПОЛНОСТЬЮ подменена) ---------------------------


def test_edit_with_text_success(client, monkeypatch):
    scene = _parsed_scene(client)
    from core.schemas import Scene as SceneModel

    fake_result = TextEditResult(
        scene=SceneModel.model_validate(scene), explanation="готово", applied=["удалён lamp_001"], rejected=[], warnings=[]
    )
    monkeypatch.setattr(editor_api, "edit_scene_with_text", lambda scene, instruction, history=(): fake_result)
    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": "убери фонарь"})
    assert r.status_code == 200
    assert r.json()["applied"] == ["удалён lamp_001"]


def test_edit_with_text_not_configured_returns_503(client, monkeypatch):
    scene = _parsed_scene(client)

    def _raise(scene, instruction, history=()):
        raise LlmNotConfiguredError("не настроено")

    monkeypatch.setattr(editor_api, "edit_scene_with_text", _raise)
    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": "что угодно"})
    assert r.status_code == 503


def test_edit_with_text_llm_error_returns_502(client, monkeypatch):
    scene = _parsed_scene(client)

    def _raise(scene, instruction, history=()):
        raise LlmError("недоступна")

    monkeypatch.setattr(editor_api, "edit_scene_with_text", _raise)
    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": "что угодно"})
    assert r.status_code == 502


def test_edit_with_text_rejects_empty_instruction(client):
    scene = _parsed_scene(client)
    r = client.post("/api/edit-with-text", json={"scene": scene, "instruction": ""})
    assert r.status_code == 422


# --- /api/export-dxf ---------------------------------------------------------


def test_export_dxf_returns_a_valid_file(client):
    scene = _parsed_scene(client)
    r = client.post("/api/export-dxf", json=scene)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/dxf"
    assert "attachment" in r.headers["content-disposition"]
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    assert len(list(doc.modelspace())) > 0


# --- Аккаунты ----------------------------------------------------------------


def test_register_then_whoami(client):
    tokens = _register(client)
    r = client.get("/api/auth/me", headers=_auth_headers(tokens))
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_register_duplicate_username_returns_400(client):
    _register(client)
    r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert r.status_code == 400


def test_register_username_too_short_is_rejected_at_request_validation(client):
    # RegisterRequest.username требует min_length=3 (accounts/schemas.py) --
    # отклоняется ещё до projects_service.register(), поэтому 422, а не 400.
    r = client.post("/api/auth/register", json={"username": "ab", "password": "password123"})
    assert r.status_code == 422


def test_register_username_with_invalid_characters_returns_400(client):
    # Достаточной длины для RegisterRequest (min_length=3), но не проходит
    # auth.USERNAME_RE (только латиница/цифры/._) -- отклоняется уже внутри
    # projects_service.register() как ProjectsError -> 400.
    r = client.post("/api/auth/register", json={"username": "имя-плохое", "password": "password123"})
    assert r.status_code == 400


def test_register_rate_limited_after_too_many_attempts(client):
    for i in range(accounts_api.REGISTER_MAX_ATTEMPTS):
        client.post("/api/auth/register", json={"username": f"user{i}", "password": "password123"})
    r = client.post("/api/auth/register", json={"username": "one_too_many", "password": "password123"})
    assert r.status_code == 429


def test_login_success(client):
    _register(client)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_login_wrong_password_returns_401(client):
    _register(client)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_rate_limited_after_too_many_attempts(client):
    _register(client)
    for _ in range(accounts_api.LOGIN_MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    r = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 429


def test_refresh_issues_new_access_token(client):
    tokens = _register(client)
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_refresh_with_garbage_token_returns_401(client):
    r = client.post("/api/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 401


def test_logout_revokes_the_session(client):
    tokens = _register(client)
    r = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    r2 = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401


def test_logout_with_garbage_token_is_still_ok(client):
    r = client.post("/api/auth/logout", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 200


def test_whoami_without_token_returns_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_whoami_with_garbage_token_returns_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_auth_endpoints_return_503_when_mongo_unavailable(client, monkeypatch):
    from pymongo.errors import PyMongoError

    async def _raise(*a, **kw):
        raise PyMongoError("недоступна")

    monkeypatch.setattr(accounts_api.projects_service, "register", _raise)
    r = client.post("/api/auth/register", json={"username": "bob", "password": "password123"})
    assert r.status_code == 503


def test_login_returns_503_when_mongo_unavailable(client, monkeypatch):
    from pymongo.errors import PyMongoError

    async def _raise(*a, **kw):
        raise PyMongoError("недоступна")

    monkeypatch.setattr(accounts_api.projects_service, "login", _raise)
    r = client.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    assert r.status_code == 503


# --- Проекты -------------------------------------------------------------


def _scene_payload(client):
    return _parsed_scene(client)


def test_projects_require_authentication(client):
    r = client.get("/api/projects")
    assert r.status_code == 401


def test_create_list_get_update_delete_project(client):
    tokens = _register(client)
    headers = _auth_headers(tokens)
    scene = _scene_payload(client)

    created = client.post("/api/projects", json={"name": "Мой двор", "scene": scene}, headers=headers)
    assert created.status_code == 200
    project_id = created.json()["id"]

    listed = client.get("/api/projects", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    fetched = client.get(f"/api/projects/{project_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Мой двор"

    updated = client.put(f"/api/projects/{project_id}", json={"name": "Новое имя"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Новое имя"

    deleted = client.delete(f"/api/projects/{project_id}", headers=headers)
    assert deleted.status_code == 200

    gone = client.get(f"/api/projects/{project_id}", headers=headers)
    assert gone.status_code == 404


def test_create_project_enforces_the_three_project_limit(client):
    tokens = _register(client)
    headers = _auth_headers(tokens)
    scene = _scene_payload(client)
    for i in range(accounts_api.projects_service.MAX_PROJECTS_PER_USER):
        r = client.post("/api/projects", json={"name": f"Проект {i}", "scene": scene}, headers=headers)
        assert r.status_code == 200
    r = client.post("/api/projects", json={"name": "Лишний", "scene": scene}, headers=headers)
    assert r.status_code == 400


def test_project_endpoints_are_isolated_per_user(client):
    scene = _scene_payload(client)
    alice = _auth_headers(_register(client, "alice"))
    bob = _auth_headers(_register(client, "bob"))

    created = client.post("/api/projects", json={"name": "Двор Алисы", "scene": scene}, headers=alice)
    project_id = created.json()["id"]

    r = client.get(f"/api/projects/{project_id}", headers=bob)
    assert r.status_code == 404

    r = client.put(f"/api/projects/{project_id}", json={"name": "Захват"}, headers=bob)
    assert r.status_code == 404

    r = client.delete(f"/api/projects/{project_id}", headers=bob)
    assert r.status_code == 404


def test_get_nonexistent_project_returns_404(client):
    tokens = _register(client)
    headers = _auth_headers(tokens)
    r = client.get("/api/projects/000000000000000000000000", headers=headers)
    assert r.status_code == 404


@pytest.mark.parametrize(
    "method,path_suffix,service_fn,body",
    [
        ("get", "", "list_projects", None),
        ("post", "", "create_project", {"name": "x", "scene": None}),
        ("get", "/000000000000000000000000", "get_project", None),
        ("put", "/000000000000000000000000", "update_project", {"name": "x"}),
        ("delete", "/000000000000000000000000", "delete_project", None),
    ],
)
def test_projects_return_503_when_mongo_unavailable(client, monkeypatch, method, path_suffix, service_fn, body):
    from pymongo.errors import PyMongoError

    tokens = _register(client)
    headers = _auth_headers(tokens)
    if body is not None and "scene" in body:
        body = {**body, "scene": _scene_payload(client)}

    async def _raise(*a, **kw):
        raise PyMongoError("недоступна")

    monkeypatch.setattr(accounts_api.projects_service, service_fn, _raise)
    r = getattr(client, method)(f"/api/projects{path_suffix}", headers=headers, **({"json": body} if body is not None else {}))
    assert r.status_code == 503


# --- /api/greenplan/document -------------------------------------------------


def test_greenplan_document_returns_docx_for_generated_plan(client):
    import io

    from docx import Document

    scene = _parsed_scene(client)
    generated = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    r = client.post(
        "/api/greenplan/document",
        json={"scene": generated["scene"], "assignments": generated["assignments"], "report": None, "title": "Двор"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    doc = Document(io.BytesIO(r.content))
    assert doc.paragraphs[0].text == "Пояснительная записка к проекту озеленения"


# --- Сжатие ответов (GZipMiddleware) ------------------------------------------


def test_scene_response_is_gzipped_when_client_accepts_it(client):
    # Сцена реального участка -- сотни КБ JSON, в gzip в 5-6 раз меньше.
    with open(ROOT_LOCATIONS[0], "rb") as f:
        r = client.post("/api/parse", files={"file": ("test.dxf", f, "application/dxf")}, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.json()["boundary"] is not None  # httpx сам распаковывает


def test_small_responses_are_not_compressed(client):
    r = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers
