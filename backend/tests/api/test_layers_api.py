"""Слои чертежа через HTTP: сводка распознавания в meta.layers, назначения
слоёв пользователем (layerMap) при загрузке и повторный разбор загруженного
чертежа (/api/reparse) -- для DXF и для пачки DWG."""

import io
import json

import ezdxf
import pytest
from fastapi.testclient import TestClient

from exchange import dwg_batch_converter
from main import app


@pytest.fixture
def client(fake_mongo, fake_redis):
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _dwg_job_in_process(monkeypatch, tmp_path):
    monkeypatch.setenv("DWG_JOB_ISOLATED", "0")
    monkeypatch.setenv("DWG_SLOT_DIR", str(tmp_path / "dwg-slots"))


def _site_doc() -> ezdxf.document.Drawing:
    """Участок, у которого граница и газон названы по-русски, а дом и
    деревья -- на слоях, которые не узнать по имени."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "Граница участка"})
    msp.add_lwpolyline([(10, 10), (30, 10), (30, 25), (10, 25)], close=True, dxfattribs={"layer": "Слой 5"})
    for x in (50, 60, 70):
        msp.add_point((x, 80), dxfattribs={"layer": "Слой 6"})
    msp.add_lwpolyline([(40, 40), (90, 40), (90, 70), (40, 70)], close=True, dxfattribs={"layer": "Травяной покров"})
    return doc


def _dxf_bytes(doc) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _upload(client, layer_map=None):
    data = {"layerMap": json.dumps(layer_map, ensure_ascii=False)} if layer_map is not None else None
    return client.post("/api/parse", files={"file": ("site.dxf", _dxf_bytes(_site_doc()), "application/dxf")}, data=data)


def _types(scene):
    return sorted(o["type"] for o in scene["objects"])


def test_layer_kinds_lists_what_a_layer_can_be(client):
    r = client.get("/api/layer-kinds")
    assert r.status_code == 200
    kinds = {k["id"]: k["label"] for k in r.json()}
    assert {"boundary", "building", "tree", "gas", "ignore"} <= set(kinds)
    assert all(kinds.values())


def test_parse_reports_how_every_layer_was_recognized(client):
    r = _upload(client)
    assert r.status_code == 200
    scene = r.json()
    layers = {layer["name"]: layer for layer in scene["meta"]["layers"]}
    assert layers["Граница участка"]["kind"] == "boundary"
    assert layers["Травяной покров"]["kind"] == "lawn" and layers["Травяной покров"]["source"] == "synonym"
    assert layers["Слой 5"]["label"] is None
    assert any("Не распознано слоёв: 2" in w for w in scene["meta"]["warnings"])
    assert _types(scene) == []


def test_parse_applies_the_users_layer_map(client):
    r = _upload(client, {"Слой 5": "building", "Слой 6": "tree"})
    assert r.status_code == 200
    scene = r.json()
    assert _types(scene) == ["building", "tree", "tree", "tree"]
    assert scene["meta"]["layerMap"] == {"Слой 5": "building", "Слой 6": "tree"}
    layers = {layer["name"]: layer for layer in scene["meta"]["layers"]}
    assert layers["Слой 5"]["source"] == "override"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"Слой 5": "skyscraper"}'])
def test_parse_rejects_a_bad_layer_map(client, raw):
    r = client.post("/api/parse", files={"file": ("site.dxf", _dxf_bytes(_site_doc()), "application/dxf")}, data={"layerMap": raw})
    assert r.status_code == 400
    assert "layerMap" in r.json()["detail"]


def test_reparse_applies_the_layer_map_to_the_stored_drawing(client):
    scene = _upload(client).json()
    r = client.post("/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "layerMap": {"Слой 5": "building"}})
    assert r.status_code == 200
    reparsed = r.json()
    assert _types(reparsed) == ["building"]
    assert reparsed["meta"]["sourceId"] == scene["meta"]["sourceId"]
    assert "lawns" in reparsed and "buildingSetbacks" in reparsed


def test_reparse_without_the_source_is_404(client):
    r = client.post("/api/reparse", json={"sourceId": "0" * 32, "layerMap": {}})
    assert r.status_code == 404


def test_reparse_rejects_unknown_kinds(client):
    scene = _upload(client).json()
    r = client.post("/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "layerMap": {"Слой 5": "skyscraper"}})
    assert r.status_code == 400


def test_export_after_assigning_layers_adds_nothing_for_unchanged_objects(client):
    # База экспорта разбирается с теми же назначениями: деревья с назначенного
    # слоя -- исходные, а не добавленные пользователем.
    scene = _upload(client, {"Слой 5": "building", "Слой 6": "tree"}).json()
    r = client.post("/api/export-dxf", json=scene)
    assert r.status_code == 200
    assert r.headers["X-GreenCity-Export"] == "overlay"
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    assert not [e for e in doc.modelspace() if e.dxf.layer.startswith(("USER_", "NEW_"))]


def test_dwg_upload_and_reparse_pass_the_layer_map_to_the_conversion(client, monkeypatch):
    seen = []

    def _merged(dwg_paths, intermediate_dir, workers=1, layer_map=None):
        seen.append(layer_map)
        result = dwg_batch_converter.BatchConversionResult(doc=_site_doc())
        result.converted = [p.name for p in dwg_paths]
        result.layer_counts = {"Рельеф": 1200}
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _merged)
    files = [("files", ("a.dwg", b"first", "application/octet-stream"))]
    r = client.post("/api/parse-dwg", files=files, data={"layerMap": json.dumps({"Слой 6": "tree"})})
    assert r.status_code == 200
    scene = r.json()
    assert _types(scene) == ["tree", "tree", "tree"]
    assert {layer["name"]: layer["entities"] for layer in scene["meta"]["layers"]}["Рельеф"] == 1200

    r = client.post("/api/reparse", json={"sourceId": scene["meta"]["sourceId"], "layerMap": {"Слой 5": "building"}})
    assert r.status_code == 200
    assert _types(r.json()) == ["building"]
    assert seen == [{"Слой 6": "tree"}, {"Слой 5": "building"}]
