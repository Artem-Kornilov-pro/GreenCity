#!/usr/bin/env python3
"""
Парсер типовых DXF-файлов (генплан двора/участка) в JSON-формат
для веб-модуля генеративного озеленения.

Использование:
    python3 parse_dxf.py input.dxf --summary
    python3 parse_dxf.py input.dxf --out-dir output

Требуется: pip install ezdxf shapely

Устройство: конфигурация слоёв -- dxf_parsing/rules.py, распознавание слоёв
с любыми именами -- dxf_parsing/layer_names.py, раскрытие блоков --
dxf_parsing/drawing.py, геометрия и Transform -- dxf_parsing/geometry.py,
зоны ограничений и граница -- dxf_parsing/zones.py (+
dxf_parsing/boundary_estimate.py, когда слоя границы нет), здания и точечные
объекты -- dxf_parsing/objects.py. Здесь -- сборка сцены (parse_dxf_doc) и
командная строка.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import ezdxf
import ezdxf.recover
import shapely
from shapely.geometry import Polygon

from dxf_parsing.boundary_estimate import (
    ESTIMATED_BOUNDARY_SOURCE_LAYER,
    _estimate_boundary_from_content,
    _estimate_fallback_region,
    _point_in_region,
    _region_to_polygon_points,
)
from dxf_parsing.drawing import DrawingView
from dxf_parsing.encoding import mojibake_encoding
from dxf_parsing.geometry import Transform, buffer_segment, centroid, polygon_points
from dxf_parsing.layer_names import LayerResolver, is_known
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
    "read_dxf",
    "reread_in_real_encoding",
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


def parse_dxf_doc(doc, scale=None, center=True, layer_map=None, dropped_layers=None):
    """Разобрать уже открытый ezdxf-документ в {boundary, restrictions, objects, meta}.
    Общее ядро для CLI (main()) и для backend/main.py (веб-эндпоинт /api/parse) —
    оба должны парсить одинаково, поэтому вся логика тут, а не продублирована.

    layer_map -- назначения пользователя {имя слоя: класс} (layer_names.LAYER_KINDS),
    главнее автоматического распознавания. dropped_layers -- слои, отброшенные
    до разбора (пачка DWG), {имя: число сущностей}: только для сводки слоёв."""
    resolver = LayerResolver(layer_map)
    msp = DrawingView.of_document(doc, resolver)
    warnings = []
    insunits = doc.header.get("$INSUNITS", 0)
    header_scale = INSUNITS_TO_METERS.get(insunits, 1.0)
    resolved_scale = scale
    if resolved_scale is None:
        resolved_scale = _detect_scale(msp, header_scale)
        if resolved_scale != header_scale:
            declared = f"в заголовке -- {_UNIT_NAMES.get(header_scale, header_scale)}" if insunits else "в заголовке единицы не заданы"
            warnings.append(f"Единицы чертежа определены по размеру участка: {_UNIT_NAMES.get(resolved_scale, resolved_scale)} ({declared}).")

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
    _to_ground_level(points, facade, _ground_elevation(msp) * resolved_scale)

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
        if boundary is not None:
            warnings.append(
                "Слой границы участка не найден: граница оценена по содержимому чертежа. "
                "Если она неверна, укажите слой границы в «Слоях чертежа»."
            )
    else:
        # С настоящей границей зоны уже обрезаны по ней; то же -- для
        # посадок, фонарей и бордюров: топосъёмка тянет объекты с соседних
        # листов за километры. И для зданий: топоплан пачки DWG приносит
        # квартал вокруг участка, а на сцене такие дома висели бы без земли.
        objects, curbs = _clip_offsite_points(objects, curbs, boundary)
        objects, restrictions, facade = _clip_offsite_buildings(objects, restrictions, facade, boundary)
        buildings = [o for o in objects if o["type"] == "building"]

    # Открытая земля -- участок минус всё известное: план покрытий часто не
    # покрывает весь участок, даже когда граница найдена по слою.
    restrictions = restrictions + _compute_ground_zone(boundary, restrictions)

    layers = msp.layer_report(dropped_layers)
    if msp.exploded_inserts:
        warnings.append(f"Раскрыто вставок блоков: {msp.exploded_inserts} -- их содержимое разобрано как обычный чертёж.")
    if msp.used_paperspace:
        warnings.append("В пространстве модели ничего не распознано: разобраны листы чертежа.")
    unrecognized = [layer for layer in layers if layer["label"] is None]
    if unrecognized:
        warnings.append(
            f"Не распознано слоёв: {len(unrecognized)} из {len(layers)}. Их содержимое не учтено -- "
            "проверьте список в «Слоях чертежа» и назначьте нужным слоям тип."
        )

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
            "layers": layers,
            "layerMap": dict(resolver.layer_map),
            "warnings": warnings,
        },
    }
    # Граница без своего слоя оценивается только по разобранной сцене, и до
    # этого сцена остаётся в координатах чертежа -- в 10-15 км от начала, где
    # float32 видеокарты теряет точность и тонкие линии мерцают. Сдвигаем
    # сцену так, чтобы центр оценённой границы был в нуле.
    if center and boundary is not None and boundary.get("sourceLayer") == ESTIMATED_BOUNDARY_SOURCE_LAYER:
        _recenter(result, boundary)
    return result


# Размер участка, который бывает у проекта благоустройства, м: от сквера до
# квартала. Чертёж, который при единицах из заголовка выходит за эти пределы,
# нарисован в других единицах (частая ошибка: миллиметры с $INSUNITS = 0 или 6).
_PLAUSIBLE_SITE_M = (15.0, 25_000.0)
_CANDIDATE_SCALES = (1.0, 0.001, 0.01, 0.1)
_UNIT_NAMES = {1.0: "метры", 0.001: "миллиметры", 0.01: "сантиметры", 0.1: "дециметры", 0.0254: "дюймы", 0.3048: "футы", 0.9144: "ярды"}


def _detect_scale(msp, header_scale):
    """Метров в единице чертежа: из заголовка ($INSUNITS), если размер участка
    при нём правдоподобен, иначе -- первые из метров, мм, см, дм, при которых
    правдоподобен."""
    extent = _site_extent(msp)
    if extent is None:
        return header_scale
    low, high = _PLAUSIBLE_SITE_M
    if low <= extent * header_scale <= high:
        return header_scale
    for candidate in _CANDIDATE_SCALES:
        if low <= extent * candidate <= high:
            return candidate
    return header_scale


def _site_extent(msp):
    """Размер участка в единицах чертежа: по границе, а без неё -- по
    распознанному содержимому (2-98 процентили координат, чтобы дальняя точка
    подложки не растянула размер). Граница ищется в единицах чертежа как в
    метрах: порог площади границы при неверных единицах её бы отбросил."""
    found = boundary_outline(msp, 1.0)
    if found is not None:
        xs, ys = [p[0] for p in found[0]], [p[1] for p in found[0]]
        return max(max(xs) - min(xs), max(ys) - min(ys))
    xs, ys = [], []
    for e in msp:
        if len(xs) >= 50_000:
            break
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT") or not is_known(msp.key(e)):
            continue
        try:
            if kind in ("LWPOLYLINE", "POLYLINE"):
                pts = polygon_points(e)
            elif kind == "LINE":
                pts = [e.dxf.start, e.dxf.end]
            elif kind in ("INSERT", "POINT", "CIRCLE"):
                pts = [e.dxf.insert if kind == "INSERT" else e.dxf.location if kind == "POINT" else e.dxf.center]
            else:
                continue
        except Exception:  # noqa: BLE001 -- битая сущность не влияет на размер
            continue
        for pt in pts:
            xs.append(pt[0])
            ys.append(pt[1])
    if len(xs) < 2:
        return None
    xs.sort()
    ys.sort()
    lo, hi = int(len(xs) * 0.02), max(int(len(xs) * 0.98) - 1, 0)
    return max(xs[hi] - xs[lo], ys[hi] - ys[lo]) or None


def _ground_elevation(msp):
    """Отметка земли участка в единицах чертежа: медиана высот распознанных
    точек, вставок, окружностей, начал отрезков и полилиний."""
    zs = []
    for e in msp:
        if len(zs) >= 50_000:
            break
        kind = e.dxftype()
        if kind not in ("INSERT", "POINT", "CIRCLE", "LINE", "LWPOLYLINE", "POLYLINE") or not is_known(msp.key(e)):
            continue
        try:
            if kind == "INSERT":
                zs.append(e.dxf.insert.z)
            elif kind == "POINT":
                zs.append(e.dxf.location.z)
            elif kind == "CIRCLE":
                zs.append(e.dxf.center.z)
            elif kind == "LINE":
                zs.append(min(e.dxf.start.z, e.dxf.end.z))
            elif kind == "LWPOLYLINE":
                zs.append(e.dxf.elevation)
            else:
                zs.append(next(iter(e.vertices)).dxf.location.z)
        except Exception:  # noqa: BLE001 -- сущность без координат не влияет на отметку
            continue
    return statistics.median(zs) if zs else 0.0


def _to_ground_level(points, facade, ground):
    """Топосъёмка в абсолютных отметках: дерево стоит на отметке 150 м и висело
    бы в воздухе. Если земля участка дальше _ABSOLUTE_ELEVATION_M от нуля, всё
    опускается на неё; ниже земли объект не уходит (точки и столбы и так
    стоят на нуле)."""
    if abs(ground) <= _ABSOLUTE_ELEVATION_M:
        return
    for o in points:
        o["position"]["y"] = round(max(o["position"]["y"] - ground, 0.0), 3)
    for quads in facade.values():
        for quad in quads:
            for p in quad:
                p["y"] = round(p["y"] - ground, 3)


_ABSOLUTE_ELEVATION_M = 5.0


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


def _relevance_region(boundary):
    """Граница участка с полосой _RESTRICTION_RELEVANCE_MARGIN_M: дальше этого
    ничто не даёт отступа посадкам на участке."""
    region = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not region.is_valid:
        region = region.buffer(0)
    region = region.buffer(_RESTRICTION_RELEVANCE_MARGIN_M)
    shapely.prepare(region)
    return region


def _touches(region, points) -> bool:
    if len(points) < 3:
        return any(shapely.contains_xy(region, p["x"], p["z"]) for p in points)
    poly = Polygon([(p["x"], p["z"]) for p in points])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return region.intersects(poly)


def _clip_offsite_buildings(objects, restrictions, facade, boundary):
    """Здания, не заходящие в полосу влияния участка, отбрасываются вместе с
    зонами, окнами и козырьками: отступа посадкам они не дают. Здание,
    которое хоть краем заходит в полосу, остаётся целиком."""
    region = _relevance_region(boundary)
    kept_objects = [
        o for o in objects
        if o["type"] != "building" or _touches(region, o["metadata"].get("footprint") or [o["position"]])
    ]
    kept_zones = [z for z in restrictions if z["type"] != "building" or _touches(region, z["polygon"])]
    # Окна и козырьки лежат на фасадах: в пределах пары метров от полосы.
    near = region.buffer(2.0)
    shapely.prepare(near)
    kept_facade = {
        key: [quad for quad in quads if any(shapely.contains_xy(near, p["x"], p["z"]) for p in quad)]
        for key, quads in facade.items()
    }
    return kept_objects, kept_zones, kept_facade


def _clip_offsite_points(objects, curbs, boundary):
    region = _relevance_region(boundary)
    kept_objects = [
        o for o in objects
        if o["type"] == "building" or shapely.contains_xy(region, o["position"]["x"], o["position"]["z"])
    ]
    kept_curbs = [c for c in curbs if any(shapely.contains_xy(region, p["x"], p["z"]) for p in c)]
    return kept_objects, kept_curbs


def read_dxf(path):
    """ezdxf-документ. Чертёж от заказчика бывает с ошибками структуры или
    кодировки -- тогда он читается в режиме восстановления, как это делает CAD.
    Если имена слоёв читаются кракозябрами (объявлена одна кодировка, записана
    другая), чертёж перечитывается в настоящей кодировке."""
    try:
        doc = ezdxf.readfile(path)
    except (UnicodeDecodeError, ezdxf.DXFStructureError):
        doc, _auditor = ezdxf.recover.readfile(path)
        return doc
    return reread_in_real_encoding(path, doc)


def reread_in_real_encoding(path, doc):
    """Тот же чертёж, прочитанный в кодировке, в которой на самом деле записаны
    имена (encoding.mojibake_encoding), или doc, если с ними всё в порядке."""
    encoding = mojibake_encoding(doc)
    if encoding is None:
        return doc
    try:
        return ezdxf.readfile(path, encoding=encoding)
    except Exception:  # noqa: BLE001 -- не перечитался: имена починит распознавание слоёв
        return doc


def parse_dxf_file(path, scale=None, center=True, layer_map=None):
    doc = read_dxf(path)
    return parse_dxf_doc(doc, scale=scale, center=center, layer_map=layer_map)


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
