"""Экспорт поверх исходного чертежа (exchange/dxf_overlay.py) и хранилище
исходников (exchange/source_store.py). ТЗ: исходные слои не изменяются,
результат -- отдельными слоями; правки пользователя -- тоже отдельно."""

import glob
from collections import Counter
from functools import cache

import ezdxf
import pytest

from core.schemas import Point3, Scene
from exchange import dwg_batch_converter, dwg_job, source_store
from exchange.dxf_overlay import overlay_scene
from exchange.dxf_parser import parse_dxf_doc, parse_dxf_file
from greenplan.options import GreenPlanOptions
from greenplan.pipeline import run_greenplan
from tests.conftest import ROOT

EPS = 1e-3


def _site_doc() -> ezdxf.document.Drawing:
    """Участок далеко от нуля (как реальные МСК-координаты) и в миллиметрах:
    экспорт обязан вернуть и сдвиг, и масштаб исходника."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4  # мм
    msp = doc.modelspace()
    ox, oy = 9_000_000.0, -8_000_000.0
    msp.add_lwpolyline([(ox, oy), (ox + 60000, oy), (ox + 60000, oy + 40000), (ox, oy + 40000)], close=True,
                       dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_lwpolyline([(ox + 5000, oy + 5000), (ox + 20000, oy + 5000), (ox + 20000, oy + 15000), (ox + 5000, oy + 15000)],
                       close=True, dxfattribs={"layer": "BUILDING"})
    msp.add_circle((ox + 40000, oy + 30000), 1500, dxfattribs={"layer": "ДЕРЕВЬЯ"})
    msp.add_circle((ox + 45000, oy + 30000), 1500, dxfattribs={"layer": "ДЕРЕВЬЯ"})
    msp.add_text("Подпись, которую парсер не читает", dxfattribs={"layer": "ПОДПИСИ", "insert": (ox + 1000, oy + 1000)})
    msp.add_line((ox, oy), (ox + 1000, oy + 1000), dxfattribs={"layer": "НЕРАСПОЗНАННЫЙ_СЛОЙ"})
    return doc


def _source_state(doc):
    msp = doc.modelspace()
    return {e.dxf.handle: (e.dxftype(), e.dxf.layer) for e in msp}


def _new_tree_at(scene: Scene, x: float, z: float, obj_id: str, generated: bool):
    base = next(o for o in scene.objects if o.type == "tree")
    meta = {"catalogId": "species_lipa_melkolistnaya", "species": "Липа мелколистная"}
    meta.update({"generated": True, "source": "greenplan", "pattern_id": "grove_clusters"} if generated else {"source": "llm"})
    return base.model_copy(update={"id": obj_id, "position": Point3(x=x, y=0, z=z), "metadata": meta})


def test_overlay_keeps_every_source_entity_and_adds_only_result_layers():
    doc = _site_doc()
    scene = Scene.model_validate(parse_dxf_doc(doc))
    before = _source_state(doc)
    edited = scene.model_copy(update={"objects": [
        *scene.objects,
        _new_tree_at(scene, 5.0, 5.0, "tree_gp_1", generated=True),
        _new_tree_at(scene, -5.0, 5.0, "tree_llm_1", generated=False),
    ]})
    summary = overlay_scene(edited, doc)
    after = _source_state(doc)
    assert all(after[h] == v for h, v in before.items()), "исходные сущности изменились"
    new_layers = Counter(layer for h, (_, layer) in after.items() if h not in before)
    assert set(new_layers) == {"NEW_TREE", "USER_TREE"}
    assert summary.layers == {"NEW_TREE": 1, "USER_TREE": 1}


def test_overlay_writes_in_source_coordinates_and_units():
    doc = _site_doc()
    scene = Scene.model_validate(parse_dxf_doc(doc))
    tree = _new_tree_at(scene, 3.0, -4.0, "tree_gp_1", generated=True)
    overlay_scene(scene.model_copy(update={"objects": [*scene.objects, tree]}), doc)
    point = next(e for e in doc.modelspace() if e.dxf.layer == "NEW_TREE" and e.dxftype() == "POINT")
    origin, scale = scene.meta.origin, scene.meta.scale
    assert abs(point.dxf.location.x - (3.0 / scale + origin["x"])) < EPS
    assert abs(point.dxf.location.y - (-4.0 / scale + origin["y"])) < EPS
    # Повторный разбор результата: новая посадка -- ровно там же в сцене.
    reparsed = Scene.model_validate(parse_dxf_doc(doc))
    assert any(abs(o.position.x - 3.0) < 0.01 and abs(o.position.z + 4.0) < 0.01 for o in reparsed.objects if o.type == "tree")
    assert point.get_xdata("GREENCITY")[0].value == "tree_gp_1"


def test_overlay_marks_moved_and_deleted_source_objects_without_touching_them():
    doc = _site_doc()
    scene = Scene.model_validate(parse_dxf_doc(doc))
    trees = [o for o in scene.objects if o.type == "tree"]
    assert len(trees) == 2
    moved = trees[0].model_copy(update={"position": Point3(x=trees[0].position.x + 2, y=0, z=trees[0].position.z)})
    kept = [o for o in scene.objects if o.id not in {trees[0].id, trees[1].id}]
    before = _source_state(doc)
    summary = overlay_scene(scene.model_copy(update={"objects": [*kept, moved]}), doc)
    assert all(_source_state(doc)[h] == v for h, v in before.items())
    # Сдвинутое -- новое место на USER_TREE и крестик на старом; удалённое -- крестик.
    assert summary.layers == {"USER_REMOVED": 2, "USER_TREE": 1}


def test_unchanged_scene_adds_nothing():
    doc = _site_doc()
    scene = Scene.model_validate(parse_dxf_doc(doc))
    before = _source_state(doc)
    assert overlay_scene(scene, doc).layers == {}
    assert _source_state(doc) == before


@cache
def _real_site_run():
    path = glob.glob(str(ROOT / "locations" / "23_*" / "*.dxf"))[0]
    scene = Scene.model_validate(parse_dxf_file(path))
    return path, run_greenplan(scene, GreenPlanOptions())


def test_greenplan_result_on_a_real_drawing():
    path, run = _real_site_run()
    doc = ezdxf.readfile(path)
    before = _source_state(doc)
    summary = overlay_scene(run.scene, doc)
    assert all(_source_state(doc)[h] == v for h, v in before.items())
    assert summary.layers.get("NEW_TREE", 0) + summary.layers.get("NEW_BUSH", 0) == len(run.new_plants)
    assert not any(layer.startswith("USER_") for layer in summary.layers)


# --- Хранилище исходников -----------------------------------------------------------


def test_source_store_round_trip_and_dedup(tmp_path):
    first = source_store.save_dxf(b"0\nSECTION\n")
    assert first == source_store.save_dxf(b"0\nSECTION\n")
    kind, path = source_store.find(first)
    assert kind == "dxf" and path.read_bytes() == b"0\nSECTION\n"


@pytest.mark.parametrize("bad", [None, "", "../etc/passwd", "a" * 31, "g" * 32, "0" * 32])
def test_source_store_rejects_foreign_or_missing_ids(bad):
    assert source_store.find(bad) is None


def test_source_store_keeps_dwg_batch_order(tmp_path):
    files = []
    for name in ("b.dwg", "a.dwg"):
        (tmp_path / name).write_bytes(name.encode())
        files.append(tmp_path / name)
    kind, paths = source_store.find(source_store.save_dwg_batch(files))
    assert kind == "dwg" and [p.name for p in paths] == ["000_b.dwg", "001_a.dwg"]


async def test_dwg_export_overlays_the_rebuilt_batch(monkeypatch, tmp_path):
    # Экспорт пачки DWG собирает её тем же merge_dwg_files, что и загрузка,
    # и дописывает слои результата (в тесте -- без дочернего процесса).
    def _merged(dwg_paths, work_dir, workers=1):
        result = dwg_batch_converter.BatchConversionResult(doc=_site_doc())
        result.converted = ["a.dwg"]
        return result

    monkeypatch.setattr(dwg_batch_converter, "merge_dwg_files", _merged)
    scene = Scene.model_validate(parse_dxf_doc(_site_doc()))
    tree = _new_tree_at(scene, 1.0, 1.0, "tree_gp_1", generated=True)
    content = await dwg_job.run_export([tmp_path / "a.dwg"], tmp_path, scene.model_copy(update={"objects": [*scene.objects, tree]}), isolated=False)
    (tmp_path / "out.dxf").write_bytes(content)
    layers = Counter(e.dxf.layer for e in ezdxf.readfile(tmp_path / "out.dxf").modelspace())
    assert layers["NEW_TREE"] == 2 and layers["ПОДПИСИ"] == 1 and layers["НЕРАСПОЗНАННЫЙ_СЛОЙ"] == 1
