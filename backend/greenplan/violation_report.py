"""
Список нарушений нормативных отступов на участке -- не скрывается, а идёт в
ответ, отчёт и записку.

Проверяются все объекты сцены, и существующие, и новые: отчёт описывает
итоговое состояние участка, а для новых посадок это независимая проверка
того, что расстановка действительно соблюла отступы. Проверяются только
отступы от зон (здания, дороги, сети); зазоры точка-точка (дерево-фонарь)
-- забота расстановки.

Кандидаты отбираются через STRtree по зонам, расширенным на худший отступ;
точное расстояние и норма считаются по исходной геометрии зоны. Объект
может нарушать несколько зон сразу -- проверяется каждая.
"""

from __future__ import annotations

from pydantic import BaseModel
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from core.schemas import Scene
from core.setback_norms import SPECIES_SETBACK_RULES, plant_kind_of_object_type, setback_for
from core.shapes import polygon_from_points

# Названия типов зон для людей -- в пояснительной записке (document.py) и в
# заметке об удалённых насаждениях (pipeline.py).
ZONE_TYPE_LABELS: dict[str, str] = {
    "building": "здания",
    "road": "проезжая часть",
    "pedestrian_path": "пешеходные дорожки и тротуары",
    "gas_pipeline": "газопровод",
    "sewer": "канализация, водосток, дренаж",
    "water_pipeline": "водопровод",
    "electrical": "силовой кабель",
    "signal_cable": "кабель связи",
    "heat_network": "теплосеть",
    "playground_zone": "детские площадки",
    "overhead_power_line": "воздушная ЛЭП",
    "transformer": "трансформаторная подстанция",
    "protected_zone": "охраняемая зона",
    "custom": "прочие зоны (парковки, неуточнённые сети)",
}


class Violation(BaseModel):
    object_id: str
    object_type: str
    zone_id: str
    zone_type: str
    severity: str  # "forbidden"/"warning" (zone.severity) -- не все нарушения одинаково критичны
    distance_m: float
    required_m: float
    message: str


def _build_zone_index(scene: Scene):
    """STRtree по расширенным зонам и параллельный список (зона, исходный
    многоугольник) или (None, []), если проверять нечего. Расширение -- худший
    отступ для дерева, куста и всех правил по породе для этого типа зоны,
    чтобы грубый отбор не потерял ни одного нарушения."""
    entries: list[tuple] = []
    buffered: list[Polygon] = []
    for zone in scene.restrictions:
        if zone.severity == "allowed" or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is None:
            continue
        reach = max(
            setback_for(zone.type, "tree", zone.minDistance),
            setback_for(zone.type, "bush", zone.minDistance),
            *(rule.distance_m for rule in SPECIES_SETBACK_RULES if rule.zone_type == zone.type),
        )
        entries.append((zone, poly))
        buffered.append(poly.buffer(reach) if reach > 0 else poly)

    if not entries:
        return None, entries
    return STRtree(buffered), entries


def find_violations(scene: Scene) -> list[Violation]:
    tree, entries = _build_zone_index(scene)
    if tree is None:
        return []

    violations: list[Violation] = []
    for obj in scene.objects:
        kind = plant_kind_of_object_type(obj.type)
        if kind is None:
            continue
        point = Point(obj.position.x, obj.position.z)
        species = obj.metadata.get("species")
        for idx in tree.query(point):
            zone, poly = entries[idx]
            required = setback_for(zone.type, kind, zone.minDistance, species=species)
            distance = poly.distance(point)
            if distance < required:
                violations.append(
                    Violation(
                        object_id=obj.id,
                        object_type=obj.type,
                        zone_id=zone.id,
                        zone_type=zone.type,
                        severity=zone.severity,
                        distance_m=round(distance, 2),
                        required_m=required,
                        message=zone.message,
                    )
                )
    return violations
