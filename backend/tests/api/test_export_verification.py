"""Независимая проверка выгрузки DXF (issue #109) и воспроизводимость --
SHA-256 исходника и версии правил (issue #110)."""

import hashlib
import io

import ezdxf
import pytest
from fastapi.testclient import TestClient

from core.versions import versions
from exchange import dxf_overlay
from main import app

SITE = "locations/location_test/01_school/01_school.dxf"


@pytest.fixture
def client(fake_mongo, fake_redis, tmp_path, monkeypatch):
    monkeypatch.setenv("GREENCITY_SOURCES_DIR", str(tmp_path / "sources"))
    with TestClient(app) as c:
        yield c


def _repo_file(path: str) -> bytes:
    from core.paths import REPO_ROOT

    return (REPO_ROOT / path).read_bytes()


def _upload(client) -> dict:
    r = client.post("/api/parse", files={"file": ("site.dxf", _repo_file(SITE), "application/dxf")})
    assert r.status_code == 200
    return r.json()


def _planted(client, scene) -> dict:
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    return result["scene"]


def test_scene_carries_the_sha256_of_the_source(client):
    scene = _upload(client)
    assert scene["meta"]["sourceSha256"] == hashlib.sha256(_repo_file(SITE)).hexdigest()


def test_greenplan_export_passes_the_independent_check(client):
    scene = _planted(client, _upload(client))
    report = client.post("/api/export-dxf/verify", json=scene).json()
    assert report["mode"] == "overlay"
    assert report["ok"], report
    assert report["source_entities_checked"] > 100
    assert (report["source_entities_missing"], report["source_entities_changed"]) == (0, 0)
    assert report["plants_checked"] > 0 and report["plants_missing"] == [] and report["plants_moved"] == []
    assert report["violations"] == []
    assert report["source_sha256"] == hashlib.sha256(_repo_file(SITE)).hexdigest()
    assert report["versions"] == versions()


def test_check_catches_a_changed_source_entity(client, monkeypatch):
    scene = _planted(client, _upload(client))
    original = dxf_overlay.overlay_scene

    def _spoil(scene_, doc):
        summary = original(scene_, doc)
        line = next(iter(doc.modelspace().query("LINE LWPOLYLINE")))
        line.dxf.layer = "ИСПОРЧЕНО"  # экспорт «случайно» тронул исходник
        return summary

    monkeypatch.setattr("api.editor.overlay_scene", _spoil)
    report = client.post("/api/export-dxf/verify", json=scene).json()
    assert not report["ok"] and report["source_entities_changed"] == 1


def test_check_catches_a_plant_that_breaks_the_norms(client):
    scene = _planted(client, _upload(client))
    building = next(z for z in scene["restrictions"] if z["type"] == "building")
    tree = next(o for o in scene["objects"] if o["type"] == "tree" and o["metadata"].get("generated"))
    tree["position"]["x"], tree["position"]["z"] = building["polygon"][0]["x"], building["polygon"][0]["z"]
    report = client.post("/api/export-dxf/verify", json=scene).json()
    assert not report["ok"]
    assert any(v["object_id"] == tree["id"] and v["zone_type"] == "building" for v in report["violations"])


def test_rebuilt_export_is_checked_too(client):
    scene = _planted(client, _upload(client))
    scene["meta"]["sourceId"] = None  # исходника на сервере нет
    report = client.post("/api/export-dxf/verify", json=scene).json()
    assert report["mode"] == "rebuilt"
    assert "собран из сцены" in report["source_check"]
    assert report["plants_checked"] > 0 and report["plants_missing"] == [] and report["plants_moved"] == []


def test_output_header_carries_the_source_hash_and_versions(client):
    scene = _upload(client)
    content = client.post("/api/export-dxf", json=scene).content
    doc = ezdxf.read(io.StringIO(content.decode("utf-8")))
    stamp = dict(doc.header.custom_vars)
    assert stamp["GreenCity.sourceSha256"] == scene["meta"]["sourceSha256"]
    assert stamp["GreenCity.app"] == versions()["app"]
    assert stamp["GreenCity.norms"] == versions()["norms"]


def test_explanations_name_the_source_hash_and_versions(client):
    scene = _upload(client)
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    body = {"scene": result["scene"], "assignments": result["assignments"], "rejections": result["rejections"]}
    site = client.post("/api/greenplan/explanations", json=body).json()["site"]
    assert site["source_sha256"] == scene["meta"]["sourceSha256"] and site["versions"] == versions()


def test_versions_are_stable():
    assert versions() == versions()
    assert set(versions()) == {"app", "parser_rules", "norms", "precedent_corpus"}
