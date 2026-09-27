#!/usr/bin/env python3
"""
Парсер типовых DXF-файлов (генплан двора/участка) в JSON-формат
для веб-модуля генеративного озеленения.

Использование:
    python3 parse_dxf.py input.dxf --summary
    python3 parse_dxf.py input.dxf --out-dir output

Требуется: pip install ezdxf shapely

Устройство: конфигурация слоёв -- dxf_parsing/rules.py, геометрия и
Transform -- dxf_parsing/geometry.py, зоны ограничений и граница --
dxf_parsing/zones.py (+ dxf_parsing/boundary_estimate.py, когда слоя границы
нет), здания и точечные объекты -- dxf_parsing/objects.py. Здесь -- сборка
сцены (parse_dxf_doc) и командная строка.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import ezdxf
import shapely
from shapely.geometry import Polygon

from dxf_parsing.boundary_estimate import (
    ESTIMATED_BOUNDARY_SOURCE_LAYER,
    _estimate_boundary_from_content,
    _estimate_fallback_region,
    _point_in_region,
    _region_to_polygon_points,
)
from dxf_parsing.geometry import Transform, buffer_segment, centroid, polygon_points
from dxf_parsing.objects import (
    clean_label,
    dedupe_point_objects,
    extract_buildings,
    extract_curb_polylines,
    extract_facade_quads,
    extract_point_objects,
    nearest_text,
)
from dxf_parsing.rules import (
    INSUNITS_TO_METERS,
    POLYGON_RULES,
    layer_matches,
    match_rule,
)
from dxf_parsing.zones import (
    _RESTRICTION_RELEVANCE_MARGIN_M,
    GROUND_ZONE_SOURCE_NAME,
    _clip_offsite_zones,
    _compute_ground_zone,
    boundary_outline,
    extract_boundary,
    extract_restrictions,
)

# Публичный интерфейс модуля: backend (exchange/dxf_parser.py) и тесты берут
# отсюда. Остальное -- из dxf_parsing/ напрямую.
__all__ = [
    "ESTIMATED_BOUNDARY_SOURCE_LAYER",
    "GROUND_ZONE_SOURCE_NAME",
    "POLYGON_RULES",
    "Transform",
    "buffer_segment",
    "centroid",
    "clean_label",
    "extract_boundary",
    "extract_buildings",
    "extract_curb_polylines",
    "extract_facade_quads",
    "extract_point_objects",
    "extract_restrictions",
    "layer_matches",
    "match_rule",
    "nearest_text",
    "parse_dxf_doc",
    "parse_dxf_file",
    "polygon_points",
    "print_summary",
]


def print_summary(doc):
    msp = doc.modelspace()
    print(f"DXF version: {doc.dxfversion}")
    insunits = doc.header.get("$INSUNITS", 0)
    print(f"$INSUNITS: {insunits} (~{INSUNITS_TO_METERS.get(insunits, 1.0)} м/ед.)")

    per_layer = {}
    for e in msp:
        per_layer.setdefault(e.dxf.layer, Counter())[e.dxftype()] += 1

    print("\nСлои и содержимое:")
    for layer, cnt in per_layer.items():
        print(f"  {layer:28s} {dict(cnt)}")

    xs, ys = [], []
    for e in msp:
        if e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            for x, y, *_ in polygon_points(e):
                xs.append(x)
                ys.append(y)
    if xs:
        print(f"\nBBox X: {min(xs):.2f} .. {max(xs):.2f}")
        print(f"BBox Y: {min(ys):.2f} .. {max(ys):.2f}")


def parse_dxf_doc(doc, scale=None, center=True):
    """Разобрать уже открытый ezdxf-документ в {boundary, restrictions, objects, meta}.
    Общее ядро для CLI (main()) и для backend/main.py (веб-эндпоинт /api/parse) —
    оба должны парсить одинаково, поэтому вся логика тут, а не продублирована."""
    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS", 0)
    resolved_scale = scale if scale is not None else INSUNITS_TO_METERS.get(insunits, 1.0)

    origin_x, origin_y = 0.0, 0.0
    if center:
        found = boundary_outline(msp, resolved_scale)
        if found is not None:
            origin_x, origin_y = centroid(found[0])

    tf = Transform(scale=resolved_scale, origin_x=origin_x, origin_y=origin_y)

    boundary = extract_boundary(msp, tf)
    restrictions = extract_restrictions(msp, tf, boundary)
    buildings = extract_buildings(msp, tf, restrictions)
    points = dedupe_point_objects(extract_point_objects(msp, tf))
    objects = buildings + points
    facade = extract_facade_quads(msp, tf)
    curbs = extract_curb_polylines(msp, tf)

    # Без слоя границы ничто не отсеивает одиночные точки, дотянутые из
    # городской подложки или соседнего листа: одна такая точка растягивает
    # сцену в разы. Оцениваем плотное ядро координат и отсекаем всё снаружи.
    if boundary is None:
        region = _estimate_fallback_region(objects, restrictions, curbs)
        if region is not None:
            objects = [o for o in objects if _point_in_region(o["position"]["x"], o["position"]["z"], region)]
            fake_boundary = {"polygon": _region_to_polygon_points(region)}
            restrictions = _clip_offsite_zones(restrictions, fake_boundary)
            curbs = [c for c in curbs if all(_point_in_region(p["x"], p["z"], region) for p in c)]

        # Без границы GreenPlan не работает. Считаем её после отсева
        # выбросов, чтобы дальняя точка не растянула контур.
        boundary = _estimate_boundary_from_content(objects, restrictions, curbs)
    else:
        # С настоящей границей зоны уже обрезаны по ней; то же -- для
        # посадок, фонарей и бордюров: топосъёмка тянет объекты с соседних
        # листов за километры. Здания не трогаем.
        objects, curbs = _clip_offsite_points(objects, curbs, boundary)

    # Открытая земля -- участок минус всё известное: план покрытий часто не
    # покрывает весь участок, даже когда граница найдена по слою.
    restrictions = restrictions + _compute_ground_zone(boundary, restrictions)

    result = {
        "boundary": boundary,
        "restrictions": restrictions,
        "objects": objects,
        "windows": facade["windows"],
        "canopies": facade["canopies"],
        "curbs": curbs,
        "meta": {
            "scale": resolved_scale,
            "insunits": insunits,
            "origin": {"x": origin_x, "y": origin_y},
            "buildingCount": len(buildings),
            "pointObjectCount": len(points),
        },
    }
    # Граница без своего слоя оценивается только по разобранной сцене, и до
    # этого сцена остаётся в координатах чертежа -- в 10-15 км от начала, где
    # float32 видеокарты теряет точность и тонкие линии мерцают. Сдвигаем
    # сцену так, чтобы центр оценённой границы был в нуле.
    if center and boundary is not None and boundary.get("sourceLayer") == ESTIMATED_BOUNDARY_SOURCE_LAYER:
        _recenter(result, boundary)
    return result


def _recenter(scene, boundary):
    pts = boundary["polygon"]
    dx = sum(p["x"] for p in pts) / len(pts)
    dz = sum(p["z"] for p in pts) / len(pts)
    seen = set()

    def shift(point):
        # footprint здания -- те же словари точек, что и контур его зоны:
        # каждую точку сдвигаем ровно один раз.
        if id(point) in seen:
            return
        seen.add(id(point))
        point["x"] = round(point["x"] - dx, 3)
        point["z"] = round(point["z"] - dz, 3)

    for p in pts:
        shift(p)
    for zone in scene["restrictions"]:
        for p in zone["polygon"]:
            shift(p)
    for obj in scene["objects"]:
        shift(obj["position"])
        for p in obj.get("metadata", {}).get("footprint") or []:
            shift(p)
    for curb in scene["curbs"]:
        for p in curb:
            shift(p)
    for quad in (*scene["windows"], *scene["canopies"]):
        for p in quad:
            shift(p)
    meta = scene["meta"]
    meta["origin"] = {
        "x": meta["origin"]["x"] + dx / meta["scale"],
        "y": meta["origin"]["y"] + dz / meta["scale"],
    }


def _clip_offsite_points(objects, curbs, boundary):
    region = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not region.is_valid:
        region = region.buffer(0)
    region = region.buffer(_RESTRICTION_RELEVANCE_MARGIN_M)
    shapely.prepare(region)
    kept_objects = [
        o for o in objects
        if o["type"] == "building" or shapely.contains_xy(region, o["position"]["x"], o["position"]["z"])
    ]
    kept_curbs = [c for c in curbs if any(shapely.contains_xy(region, p["x"], p["z"]) for p in c)]
    return kept_objects, kept_curbs


def parse_dxf_file(path, scale=None, center=True):
    doc = ezdxf.readfile(path)
    return parse_dxf_doc(doc, scale=scale, center=center)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Путь к .dxf файлу")
    ap.add_argument("--out-dir", default="output", help="Куда писать JSON (по умолчанию ./output)")
    ap.add_argument("--summary", action="store_true", help="Только показать слои/сущности, ничего не писать")
    ap.add_argument("--scale", type=float, default=None, help="Множитель до метров (по умолчанию — авто по $INSUNITS)")
    ap.add_argument("--no-center", action="store_true", help="Не центрировать координаты по границе участка")
    args = ap.parse_args()

    try:
        doc = ezdxf.readfile(args.input)
    except OSError:
        sys.exit(f"Не удалось открыть файл: {args.input}")
    except ezdxf.DXFStructureError:
        sys.exit(f"Файл повреждён или не является корректным DXF: {args.input}")

    if args.summary:
        print_summary(doc)
        return

    result = parse_dxf_doc(doc, scale=args.scale, center=not args.no_center)
    boundary, restrictions, objects, meta = (
        result["boundary"], result["restrictions"], result["objects"], result["meta"])
    buildings = [o for o in objects if o["type"] == "building"]
    points = [o for o in objects if o["type"] != "building"]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "boundary.json").write_text(
        json.dumps(boundary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "restrictions.json").write_text(
        json.dumps(restrictions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "objects.json").write_text(
        json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "facade.json").write_text(
        json.dumps({"windows": result["windows"], "canopies": result["canopies"]},
                    ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "curbs.json").write_text(
        json.dumps(result["curbs"], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Масштаб: {meta['scale']} м/ед. (INSUNITS={meta['insunits']}), "
          f"origin=({meta['origin']['x']:.2f}, {meta['origin']['y']:.2f})")
    print(f"boundary.json      — {'1 полигон' if boundary else 'не найден'}")
    print(f"restrictions.json  — {len(restrictions)} зон")
    print(f"objects.json       — {len(objects)} объектов ({len(buildings)} зданий, {len(points)} точечных)")
    print(f"facade.json        — {len(result['windows'])} окон, {len(result['canopies'])} граней козырьков")
    print(f"curbs.json         — {len(result['curbs'])} бордюров")
    print(f"\nЗаписано в {out_dir.resolve()}")


if __name__ == "__main__":
    main()
