"""Файлы проекта и доступ по ссылке (issue #150): комплект после GreenPlan,
скачивание, замена комплекта, чужой проект, ссылка. Визуализации подменены --
без сети."""

import pytest
from fastapi.testclient import TestClient

from api import project_files as project_files_api
from core.paths import REPO_ROOT
from greenplan.visualization import Visualization, VisualizationFailed
from main import app

SITE = REPO_ROOT / "locations/location_test/01_school/01_school.dxf"
KINDS = {"docx", "explanations_json", "explanations_csv", "explanations_xlsx", "dxf", "export_check", "visualization"}


@pytest.fixture
def client(fake_mongo, fake_redis, tmp_path, monkeypatch):
    monkeypatch.setenv("GREENCITY_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("GREENCITY_PROJECT_FILES_DIR", str(tmp_path / "project_files"))

    async def _fake_visualizations(scene, assignments, views, seed=1):
        return [Visualization(view, "промпт", b"\xff\xd8\xff" + view.encode()) for view in views]

    monkeypatch.setattr(project_files_api, "generate_visualizations", _fake_visualizations)
    with TestClient(app) as c:
        yield c


def _login(client, username="alice"):
    r = client.post("/api/auth/register", json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def planted(client):
    scene = client.post("/api/parse", files={"file": ("site.dxf", SITE.read_bytes(), "application/dxf")}).json()
    return client.post("/api/greenplan/generate", json={"scene": scene}).json()


def _project(client, headers, planted, name="Школа"):
    r = client.post("/api/projects", json={"name": name, "scene": planted["scene"]}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _prepare(client, headers, project_id, planted, **extra):
    body = {"scene": planted["scene"], "assignments": planted["assignments"], "rejections": planted["rejections"], **extra}
    r = client.post(f"/api/projects/{project_id}/greenplan-files", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_greenplan_result_becomes_a_set_of_project_files(client, planted, tmp_path):
    headers = _login(client)
    project_id = _project(client, headers, planted)
    result = _prepare(client, headers, project_id, planted)
    files = result["files"]
    assert result["notes"] == []
    assert {f["kind"] for f in files} == KINDS
    assert sum(f["kind"] == "visualization" for f in files) == 3
    assert all(f["name"].endswith(("— Школа.docx", "— Школа.dxf", ".json", ".csv", ".xlsx", ".jpg")) for f in files)

    docx = next(f for f in files if f["kind"] == "docx")
    r = client.get(f"/api/projects/{project_id}/files/{docx['id']}", headers=headers)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"] and "filename*=" in r.headers["content-disposition"]

    check = next(f for f in files if f["kind"] == "export_check")
    report = client.get(f"/api/projects/{project_id}/files/{check['id']}", headers=headers).json()
    assert report["ok"] and "(пройдена)" in check["name"]

    project = client.get(f"/api/projects/{project_id}", headers=headers).json()
    assert {f["id"] for f in project["files"]} == {f["id"] for f in files}
    summary = client.get("/api/projects", headers=headers).json()[0]
    assert summary["files_count"] == len(files) and summary["shared"] is False


def test_next_greenplan_run_replaces_the_set(client, planted, tmp_path):
    headers = _login(client)
    project_id = _project(client, headers, planted)
    first = _prepare(client, headers, project_id, planted)["files"]
    second = _prepare(client, headers, project_id, planted)["files"]
    assert len(second) == len(first) and not {f["id"] for f in first} & {f["id"] for f in second}
    assert client.get(f"/api/projects/{project_id}/files/{first[0]['id']}", headers=headers).status_code == 404
    on_disk = list((tmp_path / "project_files" / project_id).iterdir())
    assert len(on_disk) == len(second)


def test_failed_visualizations_are_a_note_not_an_error(client, planted, monkeypatch):
    async def _fail(*args, **kwargs):
        raise VisualizationFailed("нет доступа к Alice AI ART")

    monkeypatch.setattr(project_files_api, "generate_visualizations", _fail)
    headers = _login(client)
    project_id = _project(client, headers, planted)
    result = _prepare(client, headers, project_id, planted)
    assert any("Визуализации" in n and "Alice AI ART" in n for n in result["notes"])
    assert {f["kind"] for f in result["files"]} == KINDS - {"visualization"}
    assert _prepare(client, headers, project_id, planted, visualizations=False)["notes"] == []


def test_files_of_another_user_are_not_reachable(client, planted):
    alice = _login(client)
    project_id = _project(client, alice, planted)
    file_id = _prepare(client, alice, project_id, planted)["files"][0]["id"]
    bob = _login(client, "bob")
    assert client.get(f"/api/projects/{project_id}/files/{file_id}", headers=bob).status_code == 404
    assert client.delete(f"/api/projects/{project_id}/files/{file_id}", headers=bob).status_code == 404
    assert client.post(f"/api/projects/{project_id}/share", json={"enabled": True}, headers=bob).status_code == 404
    assert client.get(f"/api/projects/{project_id}/files/{file_id}").status_code == 401


def test_delete_file_and_project(client, planted, tmp_path):
    headers = _login(client)
    project_id = _project(client, headers, planted)
    files = _prepare(client, headers, project_id, planted)["files"]
    assert client.delete(f"/api/projects/{project_id}/files/{files[0]['id']}", headers=headers).status_code == 200
    assert len(client.get(f"/api/projects/{project_id}", headers=headers).json()["files"]) == len(files) - 1
    assert client.delete(f"/api/projects/{project_id}", headers=headers).status_code == 200
    assert not (tmp_path / "project_files" / project_id).exists()


def test_project_by_link_is_read_only_and_can_be_revoked(client, planted):
    headers = _login(client)
    project_id = _project(client, headers, planted, name="Двор")
    files = _prepare(client, headers, project_id, planted)["files"]

    token = client.post(f"/api/projects/{project_id}/share", json={"enabled": True}, headers=headers).json()["share_token"]
    assert token and client.post(f"/api/projects/{project_id}/share", json={"enabled": True}, headers=headers).json()["share_token"] == token
    assert client.get("/api/projects", headers=headers).json()[0]["shared"] is True

    shared = client.get(f"/api/shared/{token}").json()  # без входа
    assert shared["name"] == "Двор" and len(shared["scene"]["objects"]) == len(planted["scene"]["objects"])
    assert "share_token" not in shared and "id" not in shared
    xlsx = next(f for f in shared["files"] if f["kind"] == "explanations_xlsx")
    r = client.get(f"/api/shared/{token}/files/{xlsx['id']}")
    assert r.status_code == 200 and r.content[:2] == b"PK"

    assert client.post(f"/api/projects/{project_id}/share", json={"enabled": False}, headers=headers).json()["share_token"] is None
    assert client.get(f"/api/shared/{token}").status_code == 404
    assert client.get(f"/api/shared/{token}/files/{files[0]['id']}").status_code == 404
    again = client.post(f"/api/projects/{project_id}/share", json={"enabled": True}, headers=headers).json()["share_token"]
    assert again and again != token
