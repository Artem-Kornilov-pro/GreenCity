"""
Зоны ограничений и граница участка: сбор полигонов и линий по правилам
слоёв, реконструкция зданий из обрывков линий, слияние коридоров сетей и
полигонов одного слоя, обрезка сетей за пределами участка, вычисленная
"открытая земля".
"""

import math
import re
from collections import Counter

import shapely
from ezdxf import path as ezpath
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import linemerge

from dxf_parsing import networks
from dxf_parsing.geometry import buffer_segment, curve_points, polygon_points, split_holes
from dxf_parsing.layer_names import BOUNDARY_TIER_KEYS, describe_key, layer_key_of
from dxf_parsing.rules import (
    BOUNDARY_LAYER_KEYWORDS,
    HATCH_FLATTENING_DISTANCE,
    POLYGON_RULES,
    PROJECT_BOUNDARY_LAYER_KEYWORDS,
    SKIP_LAYER_KEYWORDS,
    layer_matches,
    match_rule,
)

# Контур со слоя границы работ меньше этого -- обрывок или деталь, а не
# граница участка; тогда граница оценивается по содержимому чертежа. Слои
# BOUNDARY/TERRITORY/SITE размер не проверяют.
_MIN_BOUNDARY_AREA_SQM = 2000.0
# Куски границы работ ближе этого друг к другу -- один участок (улица,
# разрезанная перекрёстками на отрезки): склеиваются в один контур.
_BOUNDARY_PIECE_CLOSING_M = 20.0
# Участки границы дальше этого от крупнейшего -- не часть того же участка.
_BOUNDARY_NEIGHBOUR_M = 200.0
# Граница со слоя, найденного по синонимам («Граница участка», «Граница»),
# меньше этого -- деталь, а не участок.
_MIN_SYNONYM_BOUNDARY_AREA_SQM = 300.0


def boundary_outline(msp, scale=1.0):
    """(вершины контура границы в координатах чертежа, имя слоя) или None.

    Граница работ в проектах -- одна полилиния, несколько контуров или россыпь
    отрезков: контуры объединяются, отрезки собираются в многоугольники,
    близкие куски склеиваются. Если участков несколько, берутся крупнейший и
    соседние с ним (сцена хранит одну границу), далёкие отбрасываются.

    Слои с прежними именами границы главнее: слой, найденный по синонимам
    (layer_names.BOUNDARY_TIER_KEYS), берётся, только если их нет."""
    found = _boundary_outline(msp, scale, lambda key: layer_matches(key, BOUNDARY_LAYER_KEYWORDS), None)
    for tier_key in BOUNDARY_TIER_KEYS:
        if found is not None:
            break
        found = _boundary_outline(msp, scale, lambda key, tier_key=tier_key: key == tier_key, _MIN_SYNONYM_BOUNDARY_AREA_SQM)
    return found


def _boundary_outline(msp, scale, is_boundary, min_area_sqm):
    pieces, lines, first_layer, first_key = [], [], None, None
    single_pts = None
    for e in msp.query("LWPOLYLINE POLYLINE LINE"):
        key = layer_key_of(msp, e)
        if not is_boundary(key):
            continue
        if first_layer is None:
            first_layer, first_key = e.dxf.layer, key
        if e.dxftype() == "LINE":
            s, en = e.dxf.start, e.dxf.end
            if (s.x, s.y) != (en.x, en.y):
                lines.append(LineString([(s.x, s.y), (en.x, en.y)]))
            continue
        pts = polygon_points(e)
        if len(pts) >= 3:
            single_pts = pts if not pieces else None
            poly = Polygon([(x, y) for x, y, *_ in pts])
            pieces.append(poly if poly.is_valid else poly.buffer(0))
        elif len(pts) == 2:
            lines.append(LineString([(x, y) for x, y, *_ in pts]))
    if min_area_sqm is not None:
        min_area = min_area_sqm
    else:
        min_area = _MIN_BOUNDARY_AREA_SQM if first_key and layer_matches(first_key, PROJECT_BOUNDARY_LAYER_KEYWORDS) else 0.0
    if single_pts is not None and len(pieces) == 1 and not lines:
        # Прежнее поведение для одной полилинии -- вершины без изменений.
        return (single_pts, first_layer) if pieces[0].area * scale * scale >= min_area else None
    if lines:
        pieces += list(shapely.polygonize(lines).geoms)
    pieces = [p for p in pieces if not p.is_empty and p.area > 0]
    if not pieces:
        return None
    closing = _BOUNDARY_PIECE_CLOSING_M / scale
    merged = shapely.union_all(pieces).buffer(closing, quad_segs=4).buffer(-closing, quad_segs=4)
    if merged.geom_type != "Polygon":
        # Далёкий кусок на том же слое -- врезка-схема или соседний лист;
        # оболочка протянула бы к нему полосу через весь город.
        parts = sorted((p for p in merged.geoms if p.geom_type == "Polygon"), key=lambda p: -p.area)
        near = _BOUNDARY_NEIGHBOUR_M / scale
        kept = [p for p in parts if p.distance(parts[0]) <= near]
        if len(kept) == 1:
            merged = kept[0]
        else:
            merged = shapely.concave_hull(MultiPoint([c for p in kept for c in p.exterior.coords]), ratio=0.2)
    if merged.is_empty or merged.geom_type != "Polygon" or merged.area <= 0 or merged.area * scale * scale < min_area:
        return None
    return [(x, y, 0.0) for x, y in merged.exterior.coords[:-1]], first_layer


def extract_boundary(msp, tf):
    found = boundary_outline(msp, tf.scale)
    if found is None:
        return None
    pts, layer = found
    return {"polygon": tf.polygon(pts), "sourceLayer": layer}


# Здание меньше этого -- обрывок контура или деталь (крыльцо, приямок), а не
# здание.
_MIN_BUILDING_AREA_SQM = 3.0


def _area(pts_xyz, scale):
    xy = [(p[0], p[1]) for p in pts_xyz]
    doubled = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))
    return abs(doubled) / 2 * scale * scale


def _add_zone(zones, idx_by_type, cfg, layer, pts_xyz, tf, key=None):
    """key -- ключ слоя (layer_names): по нему зона получает класс layerKind,
    чтобы смысл зоны (газон, парковка) не зависел от имени слоя."""
    if cfg["type"] == "building" and _area(pts_xyz, tf.scale) < _MIN_BUILDING_AREA_SQM:
        return
    idx_by_type[cfg["type"]] += 1
    zone = {
        "id": f"{cfg['type']}_{idx_by_type[cfg['type']]:03d}",
        "type": cfg["type"],
        "name": layer,
        "polygon": tf.polygon(pts_xyz),
        "severity": cfg["severity"],
        "minDistance": cfg["minDistance"],
        "message": cfg["message"],
    }
    # extra, type-specific fields (e.g. maxHeight for overhead power lines) ride
    # along unchanged -- anything in POLYGON_RULES beyond the core keys above
    for k, v in cfg.items():
        if k not in zone:
            zone[k] = v
    kind = describe_key(key)[0] if key else None
    if kind:
        zone["layerKind"] = kind
    if cfg["type"] == "overhead_power_line":
        voltage = overhead_voltage_kv(layer)
        if voltage is not None:
            zone["voltageKv"] = voltage
    zones.append(zone)


# Класс напряжения воздушной линии в имени слоя: «ВЛ 10кВ», «ЛЭП 0,4 кВ»,
# «OHL_110kV». По нему -- отступ до кроны по ПУЭ (backend/core/setback_norms.py).
_VOLTAGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв|kv)(?![a-zа-я])", re.IGNORECASE)


def overhead_voltage_kv(layer: str):
    """Напряжение воздушной линии, кВ, из имени слоя или None."""
    m = _VOLTAGE.search(layer or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 0 < value <= 1150 else None


# Насколько близко должны сойтись концы склеенной цепочки, чтобы контур
# считался замкнутым.
_BUILDING_CLOSE_GAP_M = 2.0
# Порог "это шум съёмки (забор, обрывок бордюра), а не здание" по короткой
# стороне прямоугольника-реконструкции -- тоже оттуда же.
_BUILDING_MIN_SHORT_SIDE_M = 4.0


def _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf):
    """Здания из россыпи отрезков: топопланы рисуют контур здания штриховым
    типом линии, и при конвертации DWG он распадается на отдельные LINE.
    shapely.linemerge склеивает их по совпадающим концам.

    Замкнутая цепочка берётся как есть. Разомкнутая (съёмка видит не все
    стены) заменяется минимальным повёрнутым прямоугольником; если его
    короткая сторона меньше _BUILDING_MIN_SHORT_SIDE_M, это шум, а не здание."""
    close_gap, min_short_side = _BUILDING_CLOSE_GAP_M / tf.scale, _BUILDING_MIN_SHORT_SIDE_M / tf.scale
    for (layer, key), (cfg, segments) in building_lines.items():
        if not segments:
            continue
        merged = linemerge([LineString(s) for s in segments])
        pieces = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
        for piece in pieces:
            coords = list(piece.coords)
            if len(coords) < 3:
                continue
            (x0, y0), (x1, y1) = coords[0], coords[-1]
            gap = math.hypot(x1 - x0, y1 - y0)
            if gap < close_gap:
                poly = Polygon(coords)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.geom_type != "Polygon" or poly.area <= 0:
                    continue
            else:
                hull = MultiPoint(coords).convex_hull
                if hull.geom_type != "Polygon":
                    continue
                rect = hull.minimum_rotated_rectangle
                rect_pts = list(rect.exterior.coords)[:-1]
                sides = [math.hypot(rect_pts[i][0] - rect_pts[i - 1][0], rect_pts[i][1] - rect_pts[i - 1][1]) for i in range(len(rect_pts))]
                if min(sides) < min_short_side:
                    continue
                poly = rect
            pts3 = [(x, y, 0.0) for x, y in poly.exterior.coords[:-1]]
            _add_zone(zones, idx_by_type, cfg, layer, pts3, tf, key)


# Трассы сетей приходят раздробленными: одна линия кабеля -- сотни отдельных
# LINE и полилиний. Коридор на каждый отрезок дал бы сотни тысяч зон, поэтому
# трассы объединяются по слою до расширения. Заодно буфер цельной ломаной
# накрывает клин на изломе, который отдельные прямоугольники пропускали.
# Сети, у которых бывают выноски с подписями: трубы и кабели.
_LABELLED_NETWORK_TYPES = (*networks.NETWORK_TYPES, "electrical", "signal_cable", "custom")


def _merge_corridors(zones, idx_by_type, corridors, tf, labels_by_layer=None):
    """labels_by_layer -- {имя слоя: подписи слоя (networks.LabelIndex)}: по
    ним отделяются выноски и находятся диаметры труб (dxf_parsing/networks.py).
    Трасса с подписью диаметра -- зона шириной в трубу, остальные -- прежний
    коридор 2 x minDistance."""
    labels_by_layer = labels_by_layer or {}
    for (layer, key), (cfg, lines) in corridors.items():
        if not lines:
            continue
        sized = {}
        labels = labels_by_layer.get(layer)
        if labels is not None and cfg["type"] in _LABELLED_NETWORK_TYPES:
            lines, notes = networks.split_leaders(lines, labels, tf.scale)
            if cfg["type"] in networks.NETWORK_TYPES:
                runs, sized = networks.run_widths(lines, notes, tf.scale)
            if sized:
                for run, (width, text) in sized.items():
                    run_lines = [c for c, r in zip(lines, runs, strict=True) if r == run]
                    rule = _sized_rule(cfg, width * tf.scale, text)
                    _add_corridor(zones, idx_by_type, rule, layer, key, run_lines, width / 2, tf, networks.RUN_BRIDGE_M / tf.scale)
                lines = [c for c, r in zip(lines, runs, strict=True) if r not in sized]
        _add_corridor(zones, idx_by_type, cfg, layer, key, lines, cfg["minDistance"] / tf.scale, tf)


_NETWORK_NAMES = {
    "gas_pipeline": "Газопровод",
    "sewer": "Канализация, водосток",
    "water_pipeline": "Водопровод",
    "heat_network": "Теплосеть",
}


def _sized_rule(cfg, width_m, text):
    """Правило зоны трубы известного диаметра: зона -- сама труба."""
    diameter = networks.diameter_mm(text)
    size = f"d={diameter} мм" if diameter else f"ширина {width_m:.2f} м"
    name = _NETWORK_NAMES.get(cfg["type"], "Сеть")
    return {
        **cfg,
        "minDistance": round(width_m / 2, 3),
        "message": f"{name}, {size} (подпись «{text}»): отступ посадок -- от наружной стенки трубы (СП 42.13330.2016, прим. 5 к табл. 9.1)",
    }


def _add_corridor(zones, idx_by_type, cfg, layer, key, lines, half_width, tf, close_gap=0.0):
    """close_gap -- промежутки короче этого (пунктир трубы) закрываются: зона
    трубы шириной в сантиметры иначе рвётся на штрихи."""
    if not lines:
        return
    # Расширяем каждую линию отдельно (векторизованно) и объединяем: в
    # разы быстрее, чем буфер одной общей MultiLineString. quad_segs=2 --
    # скругление на изломе порядка сантиметров, а вершин вчетверо меньше.
    # Полуширина -- в единицах чертежа.
    merged = shapely.union_all(shapely.buffer([LineString(c) for c in lines], half_width, cap_style=2, quad_segs=2))
    if close_gap > 0 and not merged.is_empty:
        merged = merged.buffer(close_gap / 2, quad_segs=2, join_style=2).buffer(-close_gap / 2, quad_segs=2, join_style=2)
    if merged.is_empty:
        return
    # Зона хранит один контур без дыр, поэтому многоугольники с дырками
    # режутся на куски (split_holes).
    for poly in split_holes(merged):
        # [:-1] -- shapely повторяет первую точку в конце кольца, а зона
        # хранится незамкнутой.
        pts = list(poly.exterior.coords)[:-1]
        if len(pts) >= 3:
            _add_zone(zones, idx_by_type, cfg, layer, pts, tf, key)


# Объединение замкнутых контуров одного слоя (газон, плитка, дорожное
# полотно): после конвертации один слой газона бывает тысячами кусков, а
# признакам участка и разбиению на зоны нужна одна общая площадь.
#
# buffer(0) чинит самопересекающиеся контуры; вырожденные (нулевой площади)
# отбрасываются. Соседние куски в исходнике сходятся с зазором в доли
# сантиметра, поэтому перед объединением делается «замыкание»: расширение на
# _POLYGON_CLOSING_GAP_M и сжатие обратно. Зазор на порядок меньше любого
# нормативного отступа, разные объекты так не склеятся. quad_segs=4 -- в
# несколько раз быстрее почти без потери точности.
_POLYGON_CLOSING_GAP_M = 0.01
_POLYGON_CLOSING_QUAD_SEGS = 4


def _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf):
    gap = _POLYGON_CLOSING_GAP_M / tf.scale
    for (layer, key), (cfg, polys) in polygon_zones.items():
        if not polys:
            continue
        buffered = shapely.buffer(polys, gap, quad_segs=_POLYGON_CLOSING_QUAD_SEGS)
        merged = shapely.union_all(buffered).buffer(-gap, quad_segs=_POLYGON_CLOSING_QUAD_SEGS)
        if merged.is_empty:
            continue
        # Дырки сохраняются нарезкой на куски -- см. _merge_corridors.
        for poly in split_holes(merged):
            pts = list(poly.exterior.coords)[:-1]
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf, key)


# Куски «открытой земли» меньше этого -- погрешность на стыке зон, не место
# под посадку.
_GROUND_ZONE_MIN_AREA_SQM = 5.0
# Имя источника у вычисленной зоны -- чтобы отличать её от зоны со слоя DXF.
GROUND_ZONE_SOURCE_NAME = "__computed_ground__"


def _compute_ground_zone(boundary, restrictions):
    """«Открытая земля» -- граница участка минус все известные зоны, включая
    газон. Если план покрытий покрывает весь участок, остатка нет; если его
    нет или он неполный, остаток становится разрешённой зоной (как слой
    GROUND): GreenPlan сажает только в разрешённых зонах.

    Работает в координатах сцены: restrictions и boundary сюда приходят уже
    преобразованными."""
    if boundary is None or len(boundary["polygon"]) < 3:
        return []
    boundary_poly = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not boundary_poly.is_valid:
        boundary_poly = boundary_poly.buffer(0)
    if boundary_poly.is_empty or boundary_poly.area == 0:
        return []

    known_polys = []
    for zone in restrictions:
        if len(zone["polygon"]) < 3:
            continue
        poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty and poly.area > 0:
            known_polys.append(poly)

    remaining = boundary_poly if not known_polys else boundary_poly.difference(shapely.union_all(known_polys))
    if remaining.is_empty:
        return []

    existing_count = sum(1 for z in restrictions if z["type"] == "protected_zone")

    result = []
    # Режем куски с дырками: иначе здания и сети внутри терялись бы и земля
    # получалась больше самого участка.
    for part in split_holes(remaining):
        if part.area < _GROUND_ZONE_MIN_AREA_SQM:
            continue
        pts = list(part.exterior.coords)[:-1]
        if len(pts) < 3:
            continue
        existing_count += 1
        result.append({
            "id": f"protected_zone_{existing_count:03d}",
            "type": "protected_zone",
            "name": GROUND_ZONE_SOURCE_NAME,
            "polygon": [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts],
            "severity": "allowed",
            "minDistance": 0.0,
            "message": "Открытая земля — вычислено как участок минус здания/дорога/тротуар",
        })
    return result


# Насколько далеко от границы участка зона ещё может влиять на посадку:
# охранный коридор до 3 м плюс отступ по виду до 5 м, с запасом в 2,5 раза.
# Цель -- отсечь городскую подложку в сотнях метров, а не точность у края.
_RESTRICTION_RELEVANCE_MARGIN_M = 20.0


def _clip_offsite_zones(zones, boundary):
    """Обрезает зоны по _RESTRICTION_RELEVANCE_MARGIN_M от границы участка.
    Именно обрезает, а не отбрасывает: сеть подложки -- один коридор в сотни
    метров, который небольшим куском заходит на участок. Здания не
    трогаются."""
    if boundary is None or len(boundary["polygon"]) < 3:
        return zones
    site = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not site.is_valid or site.area == 0:
        return zones
    region = site.buffer(_RESTRICTION_RELEVANCE_MARGIN_M)

    result = []
    for zone in zones:
        if zone["type"] == "building" or len(zone["polygon"]) < 3:
            result.append(zone)
            continue
        poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
        if not poly.is_valid or region.contains(poly):
            result.append(zone)
            continue
        clipped = poly.intersection(region)
        if clipped.is_empty:
            continue
        for part in split_holes(clipped):
            pts = list(part.exterior.coords)[:-1]
            if len(pts) < 3:
                continue
            new_zone = dict(zone)
            new_zone["polygon"] = [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts]
            result.append(new_zone)

    # id заново по типу: обрезка могла разбить зону на несколько кусков.
    idx_by_type = Counter()
    for zone in result:
        idx_by_type[zone["type"]] += 1
        zone["id"] = f"{zone['type']}_{idx_by_type[zone['type']]:03d}"
    return result


# CIRCLE на слое зоны -- площадь (круглое здание, клумба газона, площадка),
# а не точка. У сетей круг -- колодец, у ЛЭП -- опора: их коридор и так даёт
# линия.
_CIRCLE_AREA_TYPES = ("building", "protected_zone", "playground_zone", "road", "pedestrian_path", "transformer")
# Шаг аппроксимации дуг, сплайнов и окружностей отрезками, м.
_CURVE_FLATTENING_M = 0.25


def _network_labels(msp, layers):
    """{имя слоя: подписи (networks.LabelIndex)} -- для слоёв сетей."""
    found: dict[str, list] = {}
    for e in msp.query("TEXT MTEXT"):
        layer = e.dxf.layer
        if layer in layers:
            label = networks.label_of(e)
            if label is not None:
                found.setdefault(layer, []).append(label)
    return {layer: networks.LabelIndex(labels) for layer, labels in found.items()}


def extract_restrictions(msp, tf, boundary=None):
    """Зоны ограничений по правилам слоёв.

    Замкнутые LWPOLYLINE/POLYLINE и HATCH -- многоугольники, объединённые по
    слою (_merge_polygon_zones). LINE и незамкнутые полилинии -- трассы сетей,
    объединённые по слою и расширенные в коридоры шириной 2*minDistance
    (_merge_corridors). Строго вертикальные участки (стояки к зданию)
    пропускаются. Дуги, сплайны и эллипсы -- как полилинии, окружность -- как
    замкнутый контур у площадных зон (_CIRCLE_AREA_TYPES).

    Правило сущности -- по её ключу (layer_names.layer_key_of): у вида
    чертежа он учитывает синонимы имени слоя и содержимое, у голого
    modelspace -- это имя слоя. Зоны называются именем слоя.

    boundary -- граница участка в координатах сцены. Если передана, зоны
    обрезаются по ней (_clip_offsite_zones): конвертированные файлы тянут
    сети из городской подложки на сотни метров, и на узких участках их
    отступы перекрывали бы участок насквозь."""
    zones = []
    idx_by_type = Counter()
    corridors = {}
    polygon_zones = {}
    building_lines = {}

    def rule_of(e):
        key = layer_key_of(msp, e)
        if layer_matches(key, BOUNDARY_LAYER_KEYWORDS) or layer_matches(key, SKIP_LAYER_KEYWORDS):
            return None, None
        cfg = match_rule(key, POLYGON_RULES)
        return (cfg, (e.dxf.layer, key)) if cfg else (None, None)

    def add_line(group, cfg, coords):
        if len(coords) < 2:
            return
        # Здания не сливаются: из этих зон строятся объекты сцены, и два
        # соседних дома слиплись бы в одно здание.
        if cfg["type"] == "building":
            for a, b in zip(coords, coords[1:]):
                seg = buffer_segment(a[0], a[1], b[0], b[1], cfg["minDistance"] / tf.scale)
                if seg:
                    _add_zone(zones, idx_by_type, cfg, group[0], seg, tf, group[1])
            return
        corridors.setdefault(group, (cfg, []))[1].append(coords)

    def add_closed_polygon(group, cfg, pts):
        # Здания не сливаются -- по одному объекту сцены на здание.
        if cfg["type"] == "building":
            _add_zone(zones, idx_by_type, cfg, group[0], pts, tf, group[1])
            return
        poly = Polygon([(x, y) for x, y, *_ in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            return
        polygon_zones.setdefault(group, (cfg, []))[1].append(poly)

    def add_open(group, cfg, pts):
        # Ломаная режется на вертикальном стояке: склеивать соседние
        # участки через него нельзя -- получилась бы несуществующая трасса.
        run = []
        for (x1, y1, _z1), (x2, y2, _z2) in zip(pts, pts[1:]):
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                add_line(group, cfg, run)
                run = []
                continue
            if not run:
                run.append((x1, y1))
            run.append((x2, y2))
        add_line(group, cfg, run)

    for e in msp.query("LWPOLYLINE POLYLINE"):
        if e.dxftype() == "POLYLINE" and (e.is_poly_face_mesh or e.is_polygon_mesh):
            continue  # 3D-сетка (объём здания), а не контур
        cfg, group = rule_of(e)
        if not cfg:
            continue
        is_closed = e.closed if e.dxftype() == "LWPOLYLINE" else e.is_closed
        pts = polygon_points(e)
        if is_closed and len(pts) < 3:
            # Круг, нарисованный двумя вершинами с дугами (bulge).
            pts, _ = curve_points(e, _CURVE_FLATTENING_M / tf.scale)
        if is_closed:
            if len(pts) >= 3:
                add_closed_polygon(group, cfg, pts)
        else:
            add_open(group, cfg, pts)

    for e in msp.query("LINE"):
        cfg, group = rule_of(e)
        if not cfg:
            continue
        s, en = e.dxf.start, e.dxf.end
        if math.hypot(en.x - s.x, en.y - s.y) < 1e-6:
            continue
        # Отрезки зданий копятся по слою и склеиваются в контуры позже
        # (_reconstruct_buildings_from_line_fragments): коридор на каждый
        # отрезок превратил бы здание в тысячу полосок.
        if cfg["type"] == "building":
            building_lines.setdefault(group, (cfg, []))[1].append(((s.x, s.y), (en.x, en.y)))
            continue
        add_line(group, cfg, [(s.x, s.y), (en.x, en.y)])

    for e in msp.query("ARC SPLINE ELLIPSE CIRCLE"):
        cfg, group = rule_of(e)
        if not cfg:
            continue
        if e.dxftype() == "CIRCLE" and (cfg["type"] not in _CIRCLE_AREA_TYPES or _is_point_symbol(msp, e)):
            continue
        pts, closed = curve_points(e, _CURVE_FLATTENING_M / tf.scale)
        if closed and len(pts) >= 3:
            add_closed_polygon(group, cfg, pts)
        elif not closed:
            add_open(group, cfg, pts)

    # HATCH -- план покрытий часто залит штриховкой. Все контуры заливки
    # (внешние и острова вперемешку) объединяются как обычные многоугольники:
    # покрытия почти всегда без вырезов, а остров добавит лишь маленький
    # кусок той же разрешённой зоны.
    for e in msp.query("HATCH"):
        cfg, group = rule_of(e)
        if not cfg:
            continue
        for p in ezpath.from_hatch(e):
            pts = [(v.x, v.y, v.z) for v in p.flattening(distance=HATCH_FLATTENING_DISTANCE)]
            if len(pts) >= 3:
                add_closed_polygon(group, cfg, pts)

    _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf)
    _merge_corridors(zones, idx_by_type, corridors, tf, _network_labels(msp, {group[0] for group in corridors}))
    _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf)
    return _clip_offsite_zones(_dedupe_buildings(zones), boundary)


def _is_point_symbol(msp, e) -> bool:
    """Окружность со слоя, с которого берутся и точечные объекты (площадка с
    игровым оборудованием), -- знак объекта, а не площадь."""
    from dxf_parsing.layer_names import _point_rule

    return _point_rule(layer_key_of(msp, e)) is not None


# Здание, которое на эту долю площади лежит внутри другого (не меньшего), --
# то же здание второй раз.
_BUILDING_DUPLICATE_SHARE = 0.9


def _dedupe_buildings(zones):
    """Одно здание на место. Как и бордюры (objects.dedupe_curbs), контуры
    зданий приходят из нескольких файлов пачки (на Академика Понтрягина --
    124 почти совпадающих пары), и совпадающие стены и крыши мерцали
    (z-fighting). Из дублей остаётся крупнейший (при равных -- первый)."""
    buildings = [i for i, z in enumerate(zones) if z["type"] == "building" and len(z["polygon"]) >= 3]
    if len(buildings) < 2:
        return zones
    polys = []
    for i in buildings:
        poly = Polygon([(p["x"], p["z"]) for p in zones[i]["polygon"]])
        polys.append(poly if poly.is_valid else poly.buffer(0))
    tree = shapely.STRtree(polys)
    removed = set()
    for k in sorted(range(len(polys)), key=lambda k: (-polys[k].area, k)):
        if k in removed or polys[k].is_empty:
            continue
        for m in tree.query(polys[k]):
            if m == k or m in removed or polys[m].is_empty:
                continue
            if polys[m].area > polys[k].area:  # меньшее большее не поглощает
                continue
            if polys[k].intersection(polys[m]).area >= _BUILDING_DUPLICATE_SHARE * polys[m].area:
                removed.add(m)
    drop = {buildings[k] for k in removed}
    return [z for i, z in enumerate(zones) if i not in drop]
