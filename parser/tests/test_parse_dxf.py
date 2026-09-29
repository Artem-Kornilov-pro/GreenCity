"""parse_dxf.py -- сборка сцены целиком (parse_dxf_doc/parse_dxf_file) на
настоящих файлах locations/ и командная строка."""


import pytest

import parse_dxf
from parse_dxf import (
    POLYGON_RULES,
    match_rule,
    parse_dxf_doc,
    parse_dxf_file,
    print_summary,
)

# --- parse_dxf_doc / parse_dxf_file: сквозная сборка ---------------------------


def test_parse_dxf_doc_returns_expected_top_level_shape(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc)
    assert set(result.keys()) == {"boundary", "restrictions", "objects", "windows", "canopies", "curbs", "meta"}
    assert result["boundary"]["sourceLayer"] == "TERRITORY_BOUNDARY"


def test_parse_dxf_doc_centers_on_boundary_centroid_by_default(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc)
    xs = [p["x"] for p in result["boundary"]["polygon"]]
    zs = [p["z"] for p in result["boundary"]["polygon"]]
    assert min(xs) == pytest.approx(-10.0)
    assert min(zs) == pytest.approx(-10.0)
    assert result["meta"]["origin"] == {"x": 10.0, "y": 10.0}


def test_parse_dxf_doc_center_false_keeps_original_coordinates(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc, center=False)
    xs = [p["x"] for p in result["boundary"]["polygon"]]
    assert min(xs) == pytest.approx(0.0)
    assert result["meta"]["origin"] == {"x": 0.0, "y": 0.0}


def test_parse_dxf_doc_explicit_scale_overrides_insunits(empty_doc):
    result = parse_dxf_doc(empty_doc, scale=0.5)
    assert result["meta"]["scale"] == 0.5


def test_parse_dxf_doc_default_scale_comes_from_insunits(empty_doc):
    # $INSUNITS=6 -- метры, коэффициент 1.0 (см. INSUNITS_TO_METERS)
    result = parse_dxf_doc(empty_doc)
    assert result["meta"]["scale"] == 1.0


# --- CLI: print_summary / main -------------------------------------------------


def test_print_summary_runs_without_error_and_prints_bbox(empty_doc, capsys):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_1"})
    print_summary(empty_doc)
    captured = capsys.readouterr()
    assert "BBox X" in captured.out
    assert "BUILDING_1" in captured.out


def test_main_cli_writes_expected_json_files(tmp_path, monkeypatch, location_paths):
    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir)])
    parse_dxf.main()
    assert (out_dir / "boundary.json").exists()
    assert (out_dir / "restrictions.json").exists()
    assert (out_dir / "objects.json").exists()
    assert (out_dir / "facade.json").exists()


def test_main_cli_summary_mode_does_not_write_files(tmp_path, monkeypatch, location_paths, capsys):
    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir), "--summary"])
    parse_dxf.main()
    assert not out_dir.exists()
    assert "DXF version" in capsys.readouterr().out


def test_main_cli_exits_cleanly_on_missing_file(monkeypatch):
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", "/no/such/file.dxf"])
    with pytest.raises(SystemExit):
        parse_dxf.main()


def test_main_cli_exits_cleanly_on_structurally_corrupt_dxf(tmp_path, monkeypatch):
    """Файл существует и начинается как настоящий DXF, но обрублен посреди --
    ezdxf.readfile поднимает DXFStructureError, а не OSError (та ветка уже
    покрыта test_main_cli_exits_cleanly_on_missing_file)."""
    import ezdxf

    good_path = tmp_path / "good.dxf"
    ezdxf.new("R2010").saveas(good_path)
    content = good_path.read_text()
    corrupt_path = tmp_path / "corrupt.dxf"
    corrupt_path.write_text(content[: len(content) // 2])

    monkeypatch.setattr("sys.argv", ["parse_dxf.py", str(corrupt_path)])
    with pytest.raises(SystemExit):
        parse_dxf.main()


def test_main_cli_no_center_flag_disables_centering(tmp_path, monkeypatch, location_paths):
    import json

    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir), "--no-center"])
    parse_dxf.main()
    boundary = json.loads((out_dir / "boundary.json").read_text())
    centered_boundary = parse_dxf_file(location_paths[1])["boundary"]
    assert boundary["polygon"] != centered_boundary["polygon"]


def test_match_rule_heat_network_has_its_own_type():
    # Раньше "custom" -- и строка таблицы отступов СП 42 / 743-ПП для
    # теплосети (дерево 2 м, кустарник 1 м) не применялась вовсе.
    for layer in ("HEAT", "Теплосеть", "сущ_сети_теплосеть", "Теплотрасса", "Теплопровод"):
        assert match_rule(layer.upper(), POLYGON_RULES)["type"] == "heat_network", layer
    # Специфичность не сломана: водопровод остаётся водопроводом.
    assert match_rule("ВОДОПРОВОД", POLYGON_RULES)["type"] == "water_pipeline"


def test_far_buildings_are_dropped_with_their_windows_near_ones_stay(empty_doc):
    # Топоплан пачки DWG приносит квартал вокруг участка: здания за полосой
    # влияния отбрасываются вместе с окнами, здание у границы остаётся.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True, dxfattribs={"layer": "BUILDING"})
    msp.add_lwpolyline([(110, 10), (120, 10), (120, 20), (110, 20)], close=True, dxfattribs={"layer": "BUILDING"})
    msp.add_lwpolyline([(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)], close=True, dxfattribs={"layer": "BUILDING"})
    msp.add_3dface([(10, 10, 3), (15, 10, 3), (15, 10, 5), (10, 10, 5)], dxfattribs={"layer": "WINDOWS"})
    msp.add_3dface([(1000, 1000, 3), (1005, 1000, 3), (1005, 1000, 5), (1000, 1000, 5)], dxfattribs={"layer": "WINDOWS"})

    result = parse_dxf_doc(empty_doc)

    assert sum(o["type"] == "building" for o in result["objects"]) == 2
    assert sum(z["type"] == "building" for z in result["restrictions"]) == 2
    assert result["meta"]["buildingCount"] == 2
    assert len(result["windows"]) == 1
