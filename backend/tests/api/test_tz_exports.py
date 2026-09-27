"""Выгрузки по ТЗ через HTTP: DXF поверх исходного чертежа (/api/export-dxf)
и объяснение каждой посадки со ссылкой на НПА (/api/greenplan/explanations)."""

import csv
import glob
import io
import json

import ezdxf
import pytest
from fastapi.testclient import TestClient

from core.paths import LOCATIONS_DIR
from main import app

SITE = glob.glob(str(LOCATIONS_DIR / "23_*" / "*.dxf"))[0]


@pytest.fixture
def client(fake_mongo, fake_redis):
    with TestClient(app) as c:
        yield c


def _upload(client) -> dict:
    with open(SITE, "rb") as f:
        r = client.post("/api/parse", files={"file": ("site.dxf", f, "application/dxf")})
    assert r.status_code == 200
    return r.json()


def _layers(content: bytes) -> dict[str, int]:
    doc = ezdxf.read(io.StringIO(content.decode("utf-8")))
    out: dict[str, int] = {}
    for e in doc.modelspace():
        out[e.dxf.layer] = out.get(e.dxf.layer, 0) + 1
    return out


def test_parse_remembers_the_source_drawing(client):
    assert len(_upload(client)["meta"]["sourceId"]) == 32


def test_export_overlays_result_on_the_source_drawing(client):
    scene = _upload(client)
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    r = client.post("/api/export-dxf", json=result["scene"])
    assert r.status_code == 200 and r.headers["X-GreenCity-Export"] == "overlay"
    exported = _layers(r.content)
    with open(SITE, "rb") as f:
        source = _layers(f.read())
    # Исходные слои -- те же сущности в том же числе, новое -- только NEW_*.
    assert all(exported.get(layer) == n for layer, n in source.items())
    added = {layer for layer in exported if layer not in source}
    assert added and all(layer.startswith("NEW_") for layer in added)


def test_export_without_source_falls_back_to_rebuilding(client):
    scene = _upload(client)
    scene["meta"]["sourceId"] = "0" * 32
    r = client.post("/api/export-dxf", json=scene)
    assert r.status_code == 200 and r.headers["X-GreenCity-Export"] == "rebuilt"


def test_explanations_cover_every_new_plant_with_norm_references(client):
    scene = _upload(client)
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    body = {"scene": result["scene"], "assignments": result["assignments"], "rejections": result["rejections"]}
    data = client.post("/api/greenplan/explanations", json=body).json()
    new_ids = {o["id"] for o in result["scene"]["objects"] if o["metadata"].get("generated") and o["type"] in ("tree", "bush")}
    plants = {p["id"]: p for p in data["plants"]}
    assert new_ids <= set(plants)
    for plant in plants.values():
        assert plant["dxf_layer"].startswith("NEW_") and plant["reason"] and plant["species_basis"]
        assert all(c["ok"] for c in plant["constraints"]), plant["id"]
        assert all(c["norm"] for c in plant["constraints"])
    assert any("СП 42.13330.2016" in c["norm"] for p in plants.values() for c in p["constraints"])
    # Отклонённые по норме -- с пунктом НПА; зоны запрета -- для каждой сети/здания.
    assert all(r["norm"] for r in data["rejected"])
    assert data["restricted_zones"] and all(z["tree_norm"] for z in data["restricted_zones"])


def test_explanations_csv_opens_in_excel(client):
    scene = _upload(client)
    result = client.post("/api/greenplan/generate", json={"scene": scene}).json()
    body = {"scene": result["scene"], "assignments": result["assignments"], "rejections": result["rejections"]}
    r = client.post("/api/greenplan/explanations?format=csv", json=body)
    assert r.status_code == 200 and r.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    header, body_rows = rows[0], rows[1:]
    assert header[-1] == "НПА и пункт"
    statuses = {row[1] for row in body_rows}
    assert {"посажено", "запрет посадки"} <= statuses


def test_user_edits_land_on_user_layers(client):
    scene = _upload(client)
    tree = next(o for o in scene["objects"] if o["type"] == "tree")
    user_tree = json.loads(json.dumps(tree))
    user_tree["id"] = "tree_llm_test0001"
    user_tree["metadata"] = {"catalogId": "species_lipa_melkolistnaya", "species": "Липа мелколистная", "source": "llm"}
    user_tree["position"]["x"] += 1.0
    scene["objects"].append(user_tree)
    exported = _layers(client.post("/api/export-dxf", json=scene).content)
    assert exported.get("USER_TREE") == 2  # точка и кружок-маркер
