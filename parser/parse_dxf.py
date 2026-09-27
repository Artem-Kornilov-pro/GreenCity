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
    BOUNDARY_LAYER_KEYWORDS,
    INSUNITS_TO_METERS,
    POLYGON_RULES,
    layer_matches,
    match_rule,
)
from dxf_parsing.zones import (
    GROUND_ZONE_SOURCE_NAME,
    _clip_offsite_zones,
    _compute_ground_zone,
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
        for e in msp.query("LWPOLYLINE POLYLINE"):
            if layer_matches(e.dxf.layer, BOUNDARY_LAYER_KEYWORDS):
                pts = polygon_points(e)
                if pts:
                    origin_x, origin_y = centroid(pts)
                break

    tf = Transform(scale=resolved_scale, origin_x=origin_x, origin_y=origin_y)

    boundary = extract_boundary(msp, tf)
    restrictions = extract_restrictions(msp, tf, boundary)
    buildings = extract_buildings(msp, tf, restrictions)
    points = dedupe_point_objects(extract_point_objects(msp, tf))
    objects = buildings + points
    facade = extract_facade_quads(msp, tf)
    curbs = extract_curb_polylines(msp, tf)

    # Без явного слоя границы (issue #50 follow-up: реальные DWG-батчи без
    # слоя ГРАНИЦА -- см. предупреждение в docstring extract_restrictions про
    # boundary про то, откуда берётся посторонняя геометрия) ничего выше не
    # отсеивает единичные точки, случайно дотянутые из общегородской
    # подложки/чужого тайла: одна такая точка растягивает bbox сцены в разы,
    # и настоящая, корректно отмасштабированная посадка выглядит на экране
    # крошечной точкой на фоне пустоты (реальный случай -- "13_kharkovskaya":
    # 99% из 912 деревьев/кустов укладывались в область ~700×200м, но одно-два
    # дерева оказались в 8-9 км от неё). Оцениваем плотное ядро координат и
    # отсеиваем/обрезаем всё, что снаружи -- с реальной границей это уже
    # делает _clip_offsite_zones для restrictions, здесь то же самое, но для
    # всех трёх коллекций сразу и по оценке, а не по границе.
    if boundary is None:
        region = _estimate_fallback_region(objects, restrictions, curbs)
        if region is not None:
            objects = [o for o in objects if _point_in_region(o["position"]["x"], o["position"]["z"], region)]
            fake_boundary = {"polygon": _region_to_polygon_points(region)}
            restrictions = _clip_offsite_zones(restrictions, fake_boundary)
            curbs = [c for c in curbs if all(_point_in_region(p["x"], p["z"], region) for p in c)]

        # Без границы GreenPlan (characterize_site/partition_zones) не
        # работает вообще -- см. docstring _estimate_boundary_from_content.
        # Считаем её ПОСЛЕ отсева выбросов выше, чтобы редкая дальняя точка
        # не растянула и сам контур.
        boundary = _estimate_boundary_from_content(objects, restrictions, curbs)

    # Открытая земля (issue #53) -- участок минус всё уже известное. Не
    # только для сцен без явного слоя границы: план покрытий (газон/тротуар)
    # в реальных DWG-проектах часто не покрывает весь участок, даже когда
    # сама граница найдена по слою -- см. docstring _compute_ground_zone.
    restrictions = restrictions + _compute_ground_zone(boundary, restrictions)

    return {
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
