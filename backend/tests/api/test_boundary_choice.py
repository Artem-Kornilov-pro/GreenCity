"""Выбор границы участка из кандидатов (issue #116): /api/reparse с boundary,
и выбранная граница не теряется при экспорте и проверке выгрузки."""

import io

import ezdxf
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client(fake_mongo, fake_redis, tmp_path, monkeypatch):
    monkeypatch.setenv("GREENCITY_SOURCES_DIR", str(tmp_path / "sources"))
    with TestClient(app) as c:
        yield c


def _drawing() -> bytes:
    """Участок заказчика без слоя границы: контур на «Слой 3»."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(1000, 2000), (1120, 2000), (1120, 2080), (1000, 2080)], close=True, dxfattribs={"layer": "Слой 3"})
    msp.add_lwpolyline([(1020, 2020), (1040, 2020), (1040, 2040), (1020, 2040)], close=True, dxfattribs={"layer": "Здания"})
    msp.add_lwpolyline([(1060, 2010), (1110, 2010), (1110, 2070), (1060, 2070)], close=True, dxfattribs={"layer": "Газон"})
    msp.add_line((1000, 2075), (1120, 2075), dxfattribs={"layer": "Газопровод"})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _upload(client):
    r = client.post("/api/parse", files={"file": ("site.dxf", _drawing(), "application/dxf")})
    assert r.status_code == 200
    return r.json()


def test_upload_offers_candidates_and_reparse_takes_the_chosen_one(client):
    scene = _upload(client)
    candidate = scene["meta"]["boundaryCandidates"][0]
    assert candidate["layer"] == "Слой 3"
    r = client.post("/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "layerMap": {}, "boundary": candidate["points"]})
    assert r.status_code == 200
    chosen = r.json()
    assert chosen["boundary"]["sourceLayer"] == "__chosen_by_user__"
    assert chosen["meta"]["boundaryOverride"] == candidate["points"]


def test_bad_boundary_is_refused(client):
    scene = _upload(client)
    r = client.post("/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "boundary": [[0, 0], [1, 1]]})
    assert r.status_code == 422


def test_export_and_its_check_keep_the_chosen_boundary(client):
    scene = _upload(client)
    candidate = scene["meta"]["boundaryCandidates"][0]
    chosen = client.post(
        "/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "layerMap": {}, "boundary": candidate["points"]}
    ).json()
    planted = client.post("/api/greenplan/generate", json={"scene": chosen}).json()["scene"]
    report = client.post("/api/export-dxf/verify", json=planted).json()
    assert report["ok"], report
    # Экспорт поверх исходника разобрал базу с той же границей: зоны
    # чертежа не попали в слой правок пользователя.
    content = client.post("/api/export-dxf", json=planted).content
    doc = ezdxf.read(io.StringIO(content.decode("utf-8")))
    assert not list(doc.modelspace().query('*[layer=="USER_ZONES"]'))
