"""
Оценка границы участка, когда в DXF нет слоя границы (реальные DWG-батчи):
отсев выбросов -- точек из общегородской подложки за километры от участка --
и вогнутая оболочка по плотному ядру содержимого.
"""


import shapely
from shapely import concave_hull
from shapely.geometry import LineString, MultiPoint, Point, Polygon

# "Дальний выброс" по Тьюки -- за пределами IQR*_OUTLIER_IQR_FACTOR от
# межквартильного размаха (3.0 -- стандартный порог именно для "дальних", а
# не любых, выбросов, вдвое строже обычных 1.5, используемых для разметки
# отдельных точек на boxplot). Работает независимо от размера выборки, в
# отличие от прямого перцентильного индекса (int(n*0.01) даёт 0 при n<100,
# т.е. не отбрасывает вообще ничего на типичных сценах в сотни объектов).
_OUTLIER_IQR_FACTOR = 3.0
_OUTLIER_MIN_MARGIN_M = 50.0
_OUTLIER_MIN_POINTS = 10


def _robust_bounds(values):
    """Границы Тьюки [Q1 - k*IQR, Q3 + k*IQR] по отсортированному списку --
    устойчивы к единичным дальним выбросам, в отличие от честного min/max.
    Запас снизу ограничен _OUTLIER_MIN_MARGIN_M, чтобы не обрезать
    естественный разброс плотного скопления с маленьким IQR."""
    n = len(values)
    q1 = values[n // 4]
    q3 = values[(3 * n) // 4]
    margin = max((q3 - q1) * _OUTLIER_IQR_FACTOR, _OUTLIER_MIN_MARGIN_M)
    return q1 - margin, q3 + margin


def _estimate_fallback_region(objects, restrictions, curbs):
    """Прямоугольная оценка "относящейся к площадке" области, когда явного
    слоя границы участка нет вообще (см. docstring boundary в
    extract_restrictions про то, откуда берётся посторонняя геометрия).
    Без границы _clip_offsite_zones ничего не отсеивает, и единичная точка,
    случайно дотянутая из общегородской подложки/чужого тайла, растягивает
    bbox всей сцены в разы -- настоящая, корректно отмасштабированная
    посадка выглядит на экране крошечной точкой на фоне пустоты (реальный
    случай, issue #50 follow-up: "13_kharkovskaya" -- 99% из 912 деревьев/
    кустов укладывались в область ~700×200м, но одно-два дерева оказались в
    8-9 км от неё).

    Границы Тьюки по X и Z ОТДЕЛЬНО по всем собранным координатам сразу
    (объекты + вершины зон + вершины бордюров) -- честно длинный узкий
    участок (набережная, проезд) не обрезается по краям, потому что его
    точки распределены непрерывно, без разрыва, и попадают в межквартильный
    размах целиком. Возвращает None, если точек слишком мало для устойчивой
    оценки (тогда ничего не фильтруем)."""
    xs, zs = [], []
    for o in objects:
        xs.append(o["position"]["x"])
        zs.append(o["position"]["z"])
    for r in restrictions:
        for p in r["polygon"]:
            xs.append(p["x"])
            zs.append(p["z"])
    for c in curbs:
        for p in c:
            xs.append(p["x"])
            zs.append(p["z"])

    if len(xs) < _OUTLIER_MIN_POINTS:
        return None

    xs.sort()
    zs.sort()
    minx, maxx = _robust_bounds(xs)
    minz, maxz = _robust_bounds(zs)
    return (minx, maxx, minz, maxz)


def _point_in_region(x, z, region):
    minx, maxx, minz, maxz = region
    return minx <= x <= maxx and minz <= z <= maxz


def _region_to_polygon_points(region):
    minx, maxx, minz, maxz = region
    return [{"x": minx, "z": minz}, {"x": maxx, "z": minz}, {"x": maxx, "z": maxz}, {"x": minx, "z": maxz}]


# Запас вокруг concave hull содержимого при оценке границы участка -- чтобы
# не терять объекты у самого края из-за погрешности аппроксимации.
_ESTIMATED_BOUNDARY_MARGIN_M = 10.0
# ratio для shapely.concave_hull: 0 -- максимально детальный контур (ближе к
# альфа-форме), 1 -- обычный convex hull. 0.2 -- компромисс между точностью
# формы участка (важно для plantable_ratio/elongation в GreenPlan, см.
# _estimate_boundary_from_content) и устойчивостью/числом вершин контура.
_ESTIMATED_BOUNDARY_CONCAVITY_RATIO = 0.2
# По этой метке GreenPlan/фронтенд может отличить вычисленную границу от
# реально найденного в DXF слоя -- это ОЦЕНКА, не официальный кадастр.
ESTIMATED_BOUNDARY_SOURCE_LAYER = "__estimated_from_content__"


def _estimate_boundary_from_content(objects, restrictions, curbs):
    """Граница участка "по факту" -- concave hull уже отфильтрованной (см.
    _estimate_fallback_region) геометрии сцены, когда явного слоя границы в
    DXF нет вообще (issue #50 follow-up). Это не косметика: GreenPlan
    (backend/greenplan/site_characterization.py::characterize_site,
    backend/greenplan/zone_partitioning.py::partition_zones) требует scene.boundary
    ЖЁСТКО -- обе функции возвращают None/[] при boundary=None, ничего не
    пытаясь сделать с одними restrictions/objects. Реальный случай: сцена с
    904 объектами и 15355 зонами (issue #50, "13_kharkovskaya" с топопланом)
    давала 0 assignments в /api/greenplan/generate именно поэтому, а не
    из-за нехватки места, как выглядело со стороны ("места много, а не
    расставилось").

    Concave hull, а не bbox/convex hull: у вытянutой улицы bbox/hull сильно
    завышают total_area против реальной формы участка, искажая
    plantable_ratio и elongation, на которых строится классификация
    territory_type. Небольшой запас (_ESTIMATED_BOUNDARY_MARGIN_M) по краям,
    чтобы не обрезать объекты впритык к контуру из-за погрешности
    аппроксимации. Возвращает None при недостатке точек для устойчивой
    оценки формы -- тогда сцена остаётся без границы, как и раньше."""
    points = [(o["position"]["x"], o["position"]["z"]) for o in objects]
    for r in restrictions:
        for p in r["polygon"]:
            points.append((p["x"], p["z"]))
    for c in curbs:
        for p in c:
            points.append((p["x"], p["z"]))

    if len(points) < _OUTLIER_MIN_POINTS:
        return None

    hull = _data_footprint(objects, restrictions, curbs)
    if hull is None:
        hull = _concave_hull(MultiPoint(points)).buffer(_ESTIMATED_BOUNDARY_MARGIN_M)
    if hull.geom_type != "Polygon" or not hull.is_valid or hull.area <= 0:
        return None

    pts = list(hull.exterior.coords)[:-1]
    if len(pts) < 3:
        return None
    return {
        "polygon": [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts],
        "sourceLayer": ESTIMATED_BOUNDARY_SOURCE_LAYER,
    }


# "След данных": область в пределах этого расстояния от любого снятого объекта
# (сети, бордюры, здания, посадки) -- там, где съёмка вообще что-то знает.
_FOOTPRINT_RADIUS_M = 15.0
# Промежутки между кусками следа уже этого заклеиваются (двор между двумя
# трассами, газон между бортом и зданием) -- это тот же участок.
_FOOTPRINT_CLOSING_M = 25.0
# Кусок следа меньше этой доли крупнейшего -- одинокая далёкая геометрия
# (обрывок чужого листа съёмки), а не часть участка.
_FOOTPRINT_MIN_SHARE = 0.1
# Упрощение контура следа: сантиметровая точность границе не нужна, а
# вершин у объединения тысяч буферов -- десятки тысяч.
_FOOTPRINT_SIMPLIFY_M = 1.0


def _data_footprint(objects, restrictions, curbs):
    """Граница "по следу данных" -- там, где в съёмке что-то есть, а не
    вогнутая оболочка всех точек. Оболочка одним контуром перекрывала
    промежутки между листами съёмки и одинокие далёкие объекты: на Академика
    Понтрягина коридор улицы превращался в клин на 195 га с шипом за 2 км, и
    всё это заливалось "открытой землёй" и газоном GreenPlan. Здесь -- буфер
    вокруг каждого объекта, склейка мелких промежутков, отброс мелких
    одиноких кусков. Несколько крупных кусков (два участка одной улицы) --
    вогнутая оболочка только их: схема сцены хранит одну границу.
    None -- если посчитать не удалось (тогда -- прежняя оболочка точек)."""
    geoms = [Point(o["position"]["x"], o["position"]["z"]) for o in objects]
    for r in restrictions:
        if len(r["polygon"]) >= 3:
            poly = Polygon([(p["x"], p["z"]) for p in r["polygon"]])
            geoms.append(poly if poly.is_valid else poly.buffer(0))
    geoms += [LineString([(p["x"], p["z"]) for p in c]) for c in curbs if len(c) >= 2]
    geoms = [g for g in geoms if not g.is_empty]
    if not geoms:
        return None
    try:
        footprint = shapely.union_all(shapely.buffer(geoms, _FOOTPRINT_RADIUS_M, quad_segs=2))
        footprint = footprint.buffer(_FOOTPRINT_CLOSING_M, quad_segs=2).buffer(-_FOOTPRINT_CLOSING_M, quad_segs=2)
    except shapely.errors.GEOSException:
        return None
    parts = sorted(
        (p for p in getattr(footprint, "geoms", [footprint]) if p.geom_type == "Polygon" and p.area > 0),
        key=lambda p: -p.area,
    )
    if not parts:
        return None
    kept = [p for p in parts if p.area >= parts[0].area * _FOOTPRINT_MIN_SHARE]
    if len(kept) == 1:
        # Дырки следа (пустота внутри кольца трасс) -- внутри участка.
        shape = Polygon(kept[0].exterior)
    else:
        shape = _concave_hull(MultiPoint([c for p in kept for c in p.exterior.coords]))
    shape = shape.simplify(_FOOTPRINT_SIMPLIFY_M)
    return shape if shape.geom_type == "Polygon" and shape.is_valid else None


def _concave_hull(multipoint):
    try:
        # GEOS-триангуляция внутри concave_hull иногда падает на почти
        # вырожденных наборах точек (например, объекты почти на одной
        # прямой -- узкий вытянутый участок с малым числом объектов) --
        # "Tri::getAdjacent - invalid index", воспроизведено на 12 точках
        # вдоль прямой. convex_hull не строит триангуляцию Делоне вообще,
        # поэтому устойчив там, где concave_hull ломается -- ценой более
        # грубой формы (это всё равно лучше, чем совсем без границы).
        return concave_hull(multipoint, ratio=_ESTIMATED_BOUNDARY_CONCAVITY_RATIO)
    except shapely.errors.GEOSException:
        return multipoint.convex_hull
