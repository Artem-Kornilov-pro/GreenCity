"""
Зоны ограничений и граница участка: сбор полигонов и линий по правилам
слоёв, реконструкция зданий из обрывков линий, слияние коридоров сетей и
полигонов одного слоя, обрезка сетей за пределами участка, вычисленная
"открытая земля".
"""

import math
from collections import Counter

import shapely
from ezdxf import path as ezpath
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import linemerge

from dxf_parsing.geometry import buffer_segment, polygon_points, split_holes
from dxf_parsing.rules import (
    BOUNDARY_LAYER_KEYWORDS,
    HATCH_FLATTENING_DISTANCE,
    POLYGON_RULES,
    PROJECT_BOUNDARY_LAYER_KEYWORDS,
    SKIP_LAYER_KEYWORDS,
    layer_matches,
    match_rule,
)

# Граница со слоя проекта (PROJECT_BOUNDARY_LAYER_KEYWORDS) меньше этого --
# не граница участка, а обрывок или деталь (на Академика Понтрягина на слое
# "Граница работ" генплана лежит кусок в 322 м²): лучше честно оценить границу
# по содержимому. Слои BOUNDARY/TERRITORY/SITE (рукописные DXF, корпус) --
# как раньше, без проверки размера.
_MIN_BOUNDARY_AREA_SQM = 2000.0
# Куски границы работ ближе этого друг к другу -- один участок (улица,
# разрезанная перекрёстками на отрезки): склеиваются в один контур.
_BOUNDARY_PIECE_CLOSING_M = 20.0
# Участки границы дальше этого от крупнейшего -- не часть того же участка.
_BOUNDARY_NEIGHBOUR_M = 200.0


def boundary_outline(msp, scale=1.0):
    """(вершины контура границы в координатах чертежа, имя слоя) или None.

    Одна полилиния на слое границы (рукописные тестовые DXF) -- её вершины
    как есть. В реальных проектах граница работ -- несколько контуров
    (Кустанайская -- 15, Харьковская -- 4) или вовсе россыпь отрезков
    (Куликовская -- 1120 LINE): контуры объединяются, отрезки собираются в
    полигоны (polygonize), близкие куски склеиваются. Если участков всё же
    несколько -- крупнейший и соседние с ним (вогнутая оболочка, схема сцены
    хранит одну границу), далёкие отбрасываются."""
    pieces, lines, first_layer = [], [], None
    single_pts = None
    for e in msp.query("LWPOLYLINE POLYLINE LINE"):
        layer = e.dxf.layer
        if not layer_matches(layer, BOUNDARY_LAYER_KEYWORDS):
            continue
        first_layer = first_layer or layer
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
    min_area = _MIN_BOUNDARY_AREA_SQM if first_layer and layer_matches(first_layer, PROJECT_BOUNDARY_LAYER_KEYWORDS) else 0.0
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
        # Несколько участков: крупнейший и соседние с ним. Далёкий кусок на том
        # же слое -- врезка-схема или соседний лист (на Харьковской -- за 4,5
        # км), и оболочка тянула к нему полосу через весь город.
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


# Здание меньше этого -- обрывок контура или деталь ("Части зданий": крыльца,
# приямки, осколки штриховки), а не здание: на Олимпийской деревне таких было
# 3939 из 6395, и каждое получало отступ под дерево 5 м и коробку в 3D.
_MIN_BUILDING_AREA_SQM = 3.0


def _area(pts_xyz, scale):
    xy = [(p[0], p[1]) for p in pts_xyz]
    doubled = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))
    return abs(doubled) / 2 * scale * scale


def _add_zone(zones, idx_by_type, cfg, layer, pts_xyz, tf):
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
    zones.append(zone)


# Насколько близко должны сойтись концы склеенной linemerge-цепочки, чтобы
# считать контур реально замкнутым (issue #50 follow-up) -- тот же порог и
# та же причина, что у "реально замкнут vs разомкнутая топосъёмка" в ручной
# методике реконструкции зданий для этого же класса реальных DWG-проектов.
_BUILDING_CLOSE_GAP_M = 2.0
# Порог "это шум съёмки (забор, обрывок бордюра), а не здание" по короткой
# стороне прямоугольника-реконструкции -- тоже оттуда же.
_BUILDING_MIN_SHORT_SIDE_M = 4.0


def _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf):
    """Реальные топопланы Мосгеотреста часто рисуют контур здания сложным
    (штриховым) типом линии, который при конвертации DWG->DXF "взрывается" на
    тысячи отдельных LINE без какой-либо связи между собой в самом DXF
    (реальный случай, issue #50 follow-up: "13_kharkovskaya", слой "Здания" --
    1012 отдельных LINE, ни одной LWPOLYLINE). `shapely.linemerge` склеивает
    фрагменты по совпадающим концам обратно в непрерывные линии.

    Если склеенная цепочка сошлась в кольцо (концы ближе
    _BUILDING_CLOSE_GAP_M) -- берём её контур как есть, это надёжный случай.
    Если нет -- топосъёмка трассирует только видимую со стороны съёмки часть
    стен (тот же реальный эффект, что и с разомкнутыми LWPOLYLINE в другом
    проекте с этим же классом данных): честный convex hull на разомкнutой
    трассе часто вырождается в острый "парус", поэтому берём
    minimum_rotated_rectangle (честные 90° углы) и отбрасываем результат,
    если его короткая сторона меньше _BUILDING_MIN_SHORT_SIDE_M -- это шум
    съёмки, не здание."""
    for layer, (cfg, segments) in building_lines.items():
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
            if gap < _BUILDING_CLOSE_GAP_M:
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
                if min(sides) < _BUILDING_MIN_SHORT_SIDE_M:
                    continue
                poly = rect
            pts3 = [(x, y, 0.0) for x, y in poly.exterior.coords[:-1]]
            _add_zone(zones, idx_by_type, cfg, layer, pts3, tf)


# Трассы сетей приходят из DXF раздробленными: одна линия кабеля -- это сотни
# отдельных LINE и незамкнутых полилиний (в реальном файле на 20 улиц: 73881
# LINE + 88369 сегментов полилиний). Раздувать каждый отрезок в собственную
# зону -- это 150 тысяч зон вместо нескольких сотен: 41 МБ JSON и примерно
# 450 тысяч объектов three.js на фронте, на которых браузер не укладывался и в
# 64 ГБ. Объединяем ПЕРЕД раздуванием, по слоям (слой = один тип сети с одними
# и теми же параметрами охранной зоны, их всего десяток на файл).
#
# Побочно это ещё и точнее: раздельные прямоугольники не накрывали клин на
# изломе трассы, а буфер цельной ломаной накрывает.
def _merge_corridors(zones, idx_by_type, corridors, tf):
    for layer, (cfg, lines) in corridors.items():
        if not lines:
            continue
        # Раздуваем каждую линию по отдельности и объединяем результат, а НЕ
        # буферизуем одну общую MultiLineString: на слое из 31820 кабельных
        # отрезков первое занимает 0.8 с, второе -- 8.7 с при том же итоге
        # (замерено). Векторизованный shapely.buffer обрабатывает массив
        # геометрий разом, а union_all внутри делает каскадное объединение,
        # тогда как буфер общей MultiLineString заставляет GEOS считать все
        # самопересечения трассы в одном проходе.
        #
        # quad_segs=2 вместо стандартных 8: скругление на изломе трассы -- деталь
        # порядка сантиметров на фоне охранной зоны в 2-3 метра, а вершин
        # экономит вчетверо.
        merged = shapely.union_all(
            shapely.buffer([LineString(c) for c in lines], cfg["minDistance"], cap_style=2, quad_segs=2)
        )
        if merged.is_empty:
            continue
        # Схема зоны -- плоский список точек без внутренних контуров, поэтому
        # полигоны с дырками режутся на куски без дыр (split_holes). Раньше
        # дырки выбрасывались, и кольцо трассы заливалось запретом целиком.
        for poly in split_holes(merged):
            # [:-1] -- shapely замыкает кольцо повтором первой точки, а в схеме
            # зоны полигон хранится незамкнутым (так же, как его отдаёт
            # polygon_points для обычных контуров).
            pts = list(poly.exterior.coords)[:-1]
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf)


# То же дробление, что чинит _merge_corridors выше, но для УЖЕ ЗАМКНУТЫХ
# контуров (газон, тротуарная плитка, дорожное полотно) -- рукописные
# тестовые DXF (locations/location_old/) отдавали такие слои одним
# полигоном на весь участок, поэтому раньше _add_zone вызывался прямо на
# каждый замкнутый контур без объединения. Реальные конвертированные файлы
# (locations/, issue #23) это предположение ломают: на 06_kamchatskaya_ulitsa
# один слой GRASS -- это 5751 отдельный контур (по куску газона на каждый
# фрагмент благоустройства в исходных данных), и после Этап 3/4 (см.
# site_characterization.py/zone_partitioning.py) с россыпью зон такого
# размера тем более не сработать: retrieval и разбиение на geometric-type
# зоны считают на ОДНОЙ объединённой площади, а не на тысяче стыкующихся
# осколков.
#
# Ту же порцию данных портит и низкое качество самой конвертации: заметная
# часть контуров на GRASS оказывается самопересекающейся или вырожденной
# (нулевая площадь) -- на 06_kamchatskaya_ulitsa валидных контуров с ненулевой
# площадью среди 5751 меньше 600. poly.buffer(0) чинит самопересечения тем же
# приёмом, что и building_setbacks.py; неисправимо вырожденные (buffer(0)
# всё равно пуст) отбрасываются молча -- это не потерянные данные, а
# нулевая площадь, вносить в JSON нечего.
#
# Простой union_all соседних кусков не склеивает: соседние плитки/сегменты
# дороги в исходнике сходятся не край-в-край, а с зазором в доли сантиметра
# (артефакт конвертации, не реальный разрыв) -- на слое ROAD того же файла
# 1630 валидных контуров, а после union_all остаётся 908, вместо ожидаемых
# нескольких десятков связных проездов. "Замыкание" (buffer наружу на
# _POLYGON_CLOSING_GAP_M, union, buffer обратно внутрь на ту же величину)
# перекрывает такие зазоры и даёт 134 -- при росте суммарной площади всего на
# 0.9% (замерено на том же файле/слое). Величина зазора -- сантиметр, на
# порядок меньше любого нормативного отступа в setback_norms.py, поэтому
# приклеить два физически разных объекта эта операция не может.
#
# quad_segs=4 (не дефолтные 8) -- та же экономия, что и в _merge_corridors,
# и тем более нужна здесь: слоёв тут сотни-тысячи полигонов (GRASS на
# 06_kamchatskaya_ulitsa -- 5751), и unary_union после buffer(quad_segs=8)
# на них уходил в 5-10 раз дольше при не сильно лучшем результате (замерено:
# quad_segs=8 -- 433 итоговых полигона за 5.7с, quad_segs=4 -- 608 за 0.65с
# на том же слое). Буферим ВЕКТОРИЗОВАННЫМ shapely.buffer(list, ...), а не
# циклом p.buffer(...) -- тот же приём, что в _merge_corridors, там же и
# объяснение, почему это быстрее.
_POLYGON_CLOSING_GAP_M = 0.01
_POLYGON_CLOSING_QUAD_SEGS = 4


def _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf):
    for layer, (cfg, polys) in polygon_zones.items():
        if not polys:
            continue
        buffered = shapely.buffer(polys, _POLYGON_CLOSING_GAP_M, quad_segs=_POLYGON_CLOSING_QUAD_SEGS)
        merged = shapely.union_all(buffered).buffer(
            -_POLYGON_CLOSING_GAP_M, quad_segs=_POLYGON_CLOSING_QUAD_SEGS
        )
        if merged.is_empty:
            continue
        # Дырки сохраняются нарезкой на куски -- см. _merge_corridors.
        for poly in split_holes(merged):
            pts = list(poly.exterior.coords)[:-1]
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf)


# Порог площади куска "открытой земли" после вычитания всех известных зон --
# меньше отбрасываем как обрывок геометрии (погрешность буфера/пересечения
# на стыке зон), не настоящий плантируемый кусок.
_GROUND_ZONE_MIN_AREA_SQM = 5.0
# Имя источника у автоматически вычисленной зоны -- по нему GreenPlan/фронтенд
# может отличить её от зоны, реально найденной по слою DXF (issue #53).
GROUND_ZONE_SOURCE_NAME = "__computed_ground__"


def _compute_ground_zone(boundary, restrictions):
    """Недостающая "открытая земля" = граница участка (настоящая или
    оценённая, см. _estimate_boundary_from_content) минус объединение ВСЕХ
    уже известных restriction-зон -- включая уже допустимые газоны (issue
    #53). Там, где явный GROUND-слой/газон уже покрывает весь участок
    (вручную собранные locations/), остаток естественно получается пустым
    или незначительным -- ничего не дублируется. Там, где плана покрытий нет
    вообще или он покрывает участок не полностью (реальные DWG-батчи,
    issue #50), остаток становится новой allowed-зоной -- тот же смысл, что
    у ключевого слова GROUND в POLYGON_RULES выше, просто найдено
    геометрически, а не по имени слоя (см. GreenPlan/generate-greenery,
    которые сажают ТОЛЬКО внутри severity=allowed -- без этой зоны
    неклассифицированная открытая земля молча оставалась без посадки, хотя
    визуально места было много).

    Работает уже в ВЫХОДНЫХ координатах сцены (restrictions/boundary сюда
    приходят уже трансформированными -- в отличие от _add_zone выше, здесь
    NO tf.polygon() второй раз, иначе координаты исказились бы)."""
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
    # Без нарезки (split_holes) у кусков земли терялись дырки -- здания и сети
    # внутри них, и "открытая земля" суммарно выходила больше самого участка
    # (до 125% на Олимпийской деревне).
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


# Максимальная суммарная "дальнобойность" зоны от её собственной геометрии:
# minDistance при разметке (максимум в POLYGON_RULES -- 3.0, sewer/water) +
# отступ по виду посадки, добавляемый ПОЗЖЕ генератором (максимум в
# setback_norms.SETBACK_NORMS -- 5.0, building/tree) = 8.0 м в худшем случае.
# Дальше зона участка вообще не касается ни при какой посадке. Берём с
# запасом в 2.5 раза, а не впритык -- граница не всегда идеально ровная
# (углы, выступы), и обрезка не обязана быть хирургически точной, ей важно
# отсечь именно общегородскую подложку в сотнях метров, а не сэкономить
# сантиметры у самого края.
_RESTRICTION_RELEVANCE_MARGIN_M = 20.0


def _clip_offsite_zones(zones, boundary):
    """Обрезает зоны по _RESTRICTION_RELEVANCE_MARGIN_M от границы участка --
    см. докстринг boundary в extract_restrictions про то, откуда берётся
    посторонняя геометрия. Здания не трогаем: см. там же.

    ОБРЕЗАЕТ, а не просто отбрасывает целиком: сеть на общегородской
    подложке -- это один непрерывный коридор в сотни метров, который своим
    небольшим куском всё же заходит в разрешённую область (иначе не было бы
    смысла его вообще учитывать) -- intersects() на всей зоне был бы True, и
    ничего бы не отсеялось, хотя 95% её площади к участку отношения не
    имеет. intersection() с регионом оставляет только релевantный кусок,
    остальное просто не входит в результат."""
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

    # Переприсваиваем id заново по типу -- клип мог развалить одну зону на
    # несколько кусков (MultiPolygon), старые id из _add_zone на них не
    # годятся (либо дубли, либо пропуски).
    idx_by_type = Counter()
    for zone in result:
        idx_by_type[zone["type"]] += 1
        zone["id"] = f"{zone['type']}_{idx_by_type[zone['type']]:03d}"
    return result


def extract_restrictions(msp, tf, boundary=None):
    """Закрытые LWPOLYLINE/POLYLINE -> зона-полигон, объединённая по слою
    (см. _merge_polygon_zones) -- НЕ как есть по одной сущности, вопреки тому,
    что можно было бы предположить по рукописным тестовым файлам
    (locations/location_old/), где такой слой всегда ровно один полигон на
    весь участок. Реальные конвертированные DXF (locations/) это ломают:
    один слой газона может прийти тысячами мелких кусков.

    LINE и открытые (не замкнутые) POLYLINE -> трасса трубы/кабеля. Все трассы
    ОДНОГО СЛОЯ собираются вместе и раздуваются в коридоры шириной 2*minDistance
    одним буфером с объединением (см. _merge_corridors), а не по зоне на
    сущность и тем более не по зоне на звено ломаной.

    Дробление тут било трижды. По звену ломаной -- это десятки тысяч мелких зон
    на плотно оцифрованной сети, непрактично медленно для unary_union в
    generate-greenery, плюс щели и наслоения на изгибах вместо гладкого
    коридора. По сущности -- уже лучше, но конвертер выдаёт трассу разрезанной
    на сотни отдельных полилиний (17745 штук на участок в 20 улиц), и зон всё
    равно оставались тысячи. По уже ЗАМКНУТОМУ контуру (газон, плитка, дорога)
    -- третий случай, тот же симптом на других слоях (см. _merge_polygon_zones).
    Объединение по слою закрывает все три случая разом: слой -- это один тип
    зоны с одними и теми же параметрами охранной зоны.

    Строго вертикальные участки (стояки-подключения к зданию, где меняется
    только Z) пропускаются -- это не горизонтальное ограничение в плане XZ.

    boundary -- уже посчитанная extract_boundary() граница участка (в
    масштабе сцены, тем же tf). Если передана, зоны обрезаются по
    _RESTRICTION_RELEVANCE_MARGIN_M от неё в самом конце (см.
    _clip_offsite_zones) -- реальные конвертированные файлы (не рукописные
    тестовые) тянут инженерные сети из общегородской подложки без обрезки по
    границе: на 11_frunzenskaya_naberezhnaya и 13_kharkovsky_proezd (оба --
    узкие вытянутые участки, набережная и проезд) сети покрывают полосу в
    сотни метров ПО ОБЕ СТОРОНЫ от участка, и из-за узкой формы самого
    участка их отступы перекрывают его насквозь -- generate_trees находил
    место для 2 и 0 деревьев соответственно на гектарах площади. Здания в
    фильтр не попадают: сколько бы сеть ни простиралась за границу, здание
    вне участка -- это не полезная зона ограничения, а посторонний объект,
    filtering для них не нужен (в этих файлах их и не оказалось за
    границей)."""
    zones = []
    idx_by_type = Counter()
    corridors = {}
    polygon_zones = {}
    building_lines = {}

    def add_line(layer, cfg, coords):
        if len(coords) < 2:
            return
        # Здания из слияния исключены намеренно. Оно рассчитано на сети --
        # непрерывные трассы, которые и в реальности одна сущность. Здания же
        # дискретны, а extract_buildings ниже строит объекты сцены ИЗ этих зон:
        # два соседних дома ближе 2*minDistance слиплись бы в один полигон и
        # дальше в одно здание. Отрезок на слое здания остаётся отдельной зоной,
        # как было до слияния.
        if cfg["type"] == "building":
            for a, b in zip(coords, coords[1:]):
                seg = buffer_segment(a[0], a[1], b[0], b[1], cfg["minDistance"])
                if seg:
                    _add_zone(zones, idx_by_type, cfg, layer, seg, tf)
            return
        corridors.setdefault(layer, (cfg, []))[1].append(coords)

    def add_closed_polygon(layer, cfg, pts):
        # Здания -- та же причина, что и в add_line: по одному объекту сцены
        # на здание (extract_buildings), слияние соседних домов в один
        # полигон превратило бы два дома в один.
        if cfg["type"] == "building":
            _add_zone(zones, idx_by_type, cfg, layer, pts, tf)
            return
        poly = Polygon([(x, y) for x, y, *_ in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            return
        polygon_zones.setdefault(layer, (cfg, []))[1].append(poly)

    for e in msp.query("LWPOLYLINE POLYLINE"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        is_closed = e.closed if e.dxftype() == "LWPOLYLINE" else e.is_closed
        pts = polygon_points(e)
        if is_closed:
            if len(pts) >= 3:
                add_closed_polygon(layer, cfg, pts)
        else:
            # Ломаная режется на куски только там, где идёт чисто вертикальный
            # стояк: он не ограничение в плане, но и склеивать через него
            # соседние участки в одну прямую нельзя -- получилась бы трасса,
            # которой нет.
            run = []
            for (x1, y1, _z1), (x2, y2, _z2) in zip(pts, pts[1:]):
                if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                    add_line(layer, cfg, run)
                    run = []
                    continue
                if not run:
                    run.append((x1, y1))
                run.append((x2, y2))
            add_line(layer, cfg, run)

    for e in msp.query("LINE"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        s, en = e.dxf.start, e.dxf.end
        if math.hypot(en.x - s.x, en.y - s.y) < 1e-6:
            continue
        # Здания -- отдельно от общего add_line (issue #50 follow-up): в
        # реальных топопланах контур здания часто рисуется сложным
        # (штриховым) линтайпом, который при конвертации DWG->DXF "взрывается"
        # на тысячи отдельных LINE безо всякой связи между собой в самом DXF
        # (реальный случай, "13_kharkovskaya": слой "Здания" -- 1012
        # отдельных LINE, ни одной LWPOLYLINE). add_line для зданий буферизует
        # КАЖДЫЙ отрезок отдельно -- на несвязанных фрагментах превратил бы
        # одно здание в тысячу несвязанных полосок. Копим по слою, склеиваем
        # в цельные контуры позже, см. _reconstruct_buildings_from_line_fragments.
        if cfg["type"] == "building":
            building_lines.setdefault(layer, (cfg, []))[1].append(((s.x, s.y), (en.x, en.y)))
            continue
        add_line(layer, cfg, [(s.x, s.y), (en.x, en.y)])

    # HATCH -- план покрытий (газон/тротуар и т.п.) в реальных DWG-проектах
    # часто залит штриховкой, а не нарисован полигоном (issue #50 follow-up,
    # см. HATCH_FLATTENING_DISTANCE выше). Каждый boundary path заливки --
    # свой замкнутый контур; ezdxf.path.from_hatch отдаёт их все разом
    # (внешний контур + возможные острова-дыры вперемешку, без явного флага
    # "это дыра") -- как и справочный пример, из которого это переписано, не
    # различаем их и просто объединяем все контуры слоя через ту же
    # add_closed_polygon/_merge_polygon_zones, что и обычные полигоны: на
    # практике эти покрытия почти всегда без внутренних вырезов, а island-
    # контур (если и встретится) просто добавит лишний маленький кусок той же
    # разрешённой зоны, не испортит форму в целом.
    for e in msp.query("HATCH"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        for p in ezpath.from_hatch(e):
            pts = [(v.x, v.y, v.z) for v in p.flattening(distance=HATCH_FLATTENING_DISTANCE)]
            if len(pts) >= 3:
                add_closed_polygon(layer, cfg, pts)

    _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf)
    _merge_corridors(zones, idx_by_type, corridors, tf)
    _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf)
    return _clip_offsite_zones(_dedupe_buildings(zones), boundary)


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
