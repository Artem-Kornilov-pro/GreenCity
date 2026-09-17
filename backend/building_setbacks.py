"""Кольца нормативных отступов вокруг зданий -- для отображения на сцене.

parser/parse_dxf.py отдаёт зону "building" как есть, контуром самой стены, без
визуального запаса. Реальный же запрет на посадку простирается за неё (5 м для
дерева, 1.5 м для кустарника -- setback_norms.py), и раньше это было видно
только в расчётах: у дома либо не подсвечивалось ничего, либо казалось, что
зона равна контуру.

Считается здесь, а не на фронтенде, как было раньше: там стоял самописный
outward-буфер (обход вершин со скруглением на выпуклых углах и митром на
вогнутых), который разрешает пересечения только локально, в пределах одной
вершины. На сложных контурах его результат самопересекался -- на реальном файле
из 20 улиц битым оказалось 21 кольцо из 582, и выглядело это как искривлённые
зоны у отдельных домов. Буфер shapely/GEOS разрешает самопересечения глобально
и таких артефактов не даёт.

ВАЖНО: эти зоны -- только для отображения. Подмешивать их в scene.restrictions
нельзя: проверка нарушений (frontend/src/geometry.ts::checkViolations и
backend/placement.py) и так применяет каталог норм к настоящей зоне "building",
и добавление тех же колец туда задвоило бы и предупреждения, и запреты.
"""

from typing import Any

from setback_norms import setback_for
from shapely.geometry import Polygon


def _ring(footprint: list[dict], distance: float) -> list[dict] | None:
    if len(footprint) < 3 or distance <= 0:
        return None
    poly = Polygon([(p["x"], p["z"]) for p in footprint])
    if not poly.is_valid:
        # Контур из DXF может приходить самопересекающимся; buffer(0) -- принятый
        # в GEOS способ привести такой полигон к корректному виду.
        poly = poly.buffer(0)
        if poly.is_empty:
            return None
    # quad_segs=4 вместо стандартных 8: на выпуклом углу дуга радиуса 5 м
    # срезается меньше чем на 10 см -- на просвечивающем кольце это незаметно,
    # а вершин вдвое меньше. На большой сцене (582 кольца) разница ощутима:
    # это чисто отображаемые данные, и раздувать ими сцену незачем.
    grown = poly.buffer(distance, quad_segs=4)
    if grown.is_empty:
        return None
    # Наружный буфер связного контура связен, но после чистки buffer(0) выше
    # исходник мог распасться на части -- тогда берём наибольшую.
    if grown.geom_type == "MultiPolygon":
        grown = max(grown.geoms, key=lambda g: g.area)
    return [{"x": round(x, 3), "z": round(z, 3)} for x, z in list(grown.exterior.coords)[:-1]]


def compute_building_setbacks(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Третий аргумент -- запасное значение для зон, которых нет в таблице норм;
    # "building" в ней есть, так что реально берутся табличные 5.0/1.5.
    tree_distance = setback_for("building", "tree", 5.0)
    bush_distance = setback_for("building", "bush", 1.5)
    zones: list[dict[str, Any]] = []

    for obj in objects:
        if obj.get("type") != "building":
            continue
        metadata = obj.get("metadata") or {}
        footprint = metadata.get("footprint") or []
        name = metadata.get("name") or obj.get("id", "")

        tree_ring = _ring(footprint, tree_distance)
        if tree_ring:
            zones.append({
                "id": f"setback_tree_{obj['id']}",
                "type": "building_setback_tree",
                "name": f"Отступ для дерева — {name}",
                "polygon": tree_ring,
                "severity": "warning",
                "minDistance": tree_distance,
                "message": (
                    f"До {tree_distance} м от здания «{name}» деревья сажать нельзя "
                    f"(кустарник — можно, если дальше {bush_distance} м от стены)"
                ),
            })

        bush_ring = _ring(footprint, bush_distance)
        if bush_ring:
            zones.append({
                "id": f"setback_bush_{obj['id']}",
                "type": "building_setback_bush",
                "name": f"Отступ для кустарника — {name}",
                "polygon": bush_ring,
                "severity": "forbidden",
                "minDistance": bush_distance,
                "message": f"До {bush_distance} м от здания «{name}» нельзя сажать ни дерево, ни кустарник",
            })

    return zones
