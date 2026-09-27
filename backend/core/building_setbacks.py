"""
Кольца нормативных отступов вокруг зданий -- только для отображения: 5 м
для дерева, 1,5 м для кустарника. В scene.restrictions их не добавлять --
проверка норм и так применяется к зоне здания, и нарушения задвоились бы.
"""

from typing import Any

from shapely.geometry import Polygon

from core.setback_norms import setback_for


def _ring(footprint: list[dict], distance: float) -> list[dict] | None:
    if len(footprint) < 3 or distance <= 0:
        return None
    poly = Polygon([(p["x"], p["z"]) for p in footprint])
    if not poly.is_valid:
        # Самопересекающийся контур из DXF.
        poly = poly.buffer(0)
        if poly.is_empty:
            return None
    # quad_segs=4: на кольце для отображения разница незаметна, а вершин вдвое меньше.
    grown = poly.buffer(distance, quad_segs=4)
    if grown.is_empty:
        return None
    if grown.geom_type == "MultiPolygon":
        grown = max(grown.geoms, key=lambda g: g.area)
    return [{"x": round(x, 3), "z": round(z, 3)} for x, z in list(grown.exterior.coords)[:-1]]


def compute_building_setbacks(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
