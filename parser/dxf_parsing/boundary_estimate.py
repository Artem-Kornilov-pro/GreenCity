"""
Оценка границы участка, когда в DXF нет слоя границы (реальные DWG-батчи):
отсев выбросов -- точек из общегородской подложки за километры от участка --
и вогнутая оболочка по плотному ядру содержимого.
"""


import shapely
from shapely import concave_hull
from shapely.geometry import LineString, MultiPoint, Point, Polygon

# «Дальний выброс» по Тьюки: дальше IQR * _OUTLIER_IQR_FACTOR от квартилей.
# В отличие от перцентилей, работает и на выборках в сотни точек.
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
    """Прямоугольная область, к которой относится содержимое чертежа, когда
    слоя границы нет: одиночная точка из городской подложки иначе растянула
    бы сцену в разы.

    Границы Тьюки по X и Z отдельно, по всем координатам (объекты, вершины
    зон и бордюров); длинный узкий участок не обрезается -- его точки идут
    непрерывно. None -- точек слишком мало для оценки."""
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
# ratio для shapely.concave_hull: 0 -- самый детальный контур, 1 -- выпуклая
# оболочка. 0.2 -- баланс точности формы и устойчивости.
_ESTIMATED_BOUNDARY_CONCAVITY_RATIO = 0.2
# По этой метке GreenPlan/фронтенд может отличить вычисленную границу от
# реально найденного в DXF слоя -- это ОЦЕНКА, не официальный кадастр.
ESTIMATED_BOUNDARY_SOURCE_LAYER = "__estimated_from_content__"


def _estimate_boundary_from_content(objects, restrictions, curbs):
    """Граница участка по содержимому, когда слоя границы в DXF нет: без
    границы GreenPlan не работает. Вогнутая оболочка, а не рамка: у
    вытянутой улицы рамка сильно завышает площадь и искажает признаки
    участка. Небольшой запас по краям (_ESTIMATED_BOUNDARY_MARGIN_M). None --
    точек слишком мало."""
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
    """Граница по следу данных: буфер вокруг каждого объекта, склейка
    мелких промежутков, отброс мелких одиноких кусков. Оболочка всех точек
    перекрывала бы промежутки между листами съёмки и далёкие объекты.
    Несколько крупных кусков -- вогнутая оболочка только их. None -- посчитать
    не удалось (тогда -- оболочка точек)."""
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
        # Триангуляция в concave_hull падает на почти вырожденных наборах
        # (точки почти на одной прямой); выпуклая оболочка грубее, но
        # устойчива.
        return concave_hull(multipoint, ratio=_ESTIMATED_BOUNDARY_CONCAVITY_RATIO)
    except shapely.errors.GEOSException:
        return multipoint.convex_hull
