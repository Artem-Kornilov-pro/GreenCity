"""
Список нарушений норм -- GreenPlan, Этап 6 (issue #23: "Список нарушений
норм / арифметических нестыковок (не скрывать)"). Арифметических нестыковок
считать сейчас неоткуда -- компенсационный расчёт (Этап 2) не в объёме той же
причине, что и вся ведомость: на вход подаётся только DXF, без customer-
файлов. Остаётся содержательная часть -- нарушения нормативных отступов.

НЕ через placement.Placer -- тот считает область, куда МОЖНО поставить НОВЫЙ
объект (с кэшами и prepared-геометрией под многократные проверки), а задача
здесь другая: что из УЖЕ СТОЯЩЕГО в сцене нарушает. Тот же прямой расчёт
(scene.restrictions + setback_norms.setback_for), что раньше был написан как
одноразовая независимая проверка в
tests/test_deterministic_placement.py::_independent_zone_violations -- здесь
он становится настоящей продакшен-функцией, а не только тестовым дублем.

Проверяются ВСЕ объекты сцены (и существующие, и то, что сгенерировал сам
GreenPlan) -- отчёт должен быть честным о финальном состоянии участка
целиком, а не только про то, что было "до" генерации; заодно это независимая
проверка того, что у новых объектов нарушений действительно 0 (расстановка
и так проверяет отступы по ходу дела, см. deterministic_placement.py), а не
слепое доверие этому факту.

Только зональные нарушения (здание/дорога/сети и их отступ) -- не
точка-точка (дерево-фонарь и т.п., placement.POINT_CLEARANCE_M): это другой
класс проверки, issue его в контексте существующих объектов не упоминает, и
прошлая сессия тоже не разбирала его как "нарушение нормы".
"""

from __future__ import annotations

from pydantic import BaseModel
from schemas import Scene
from setback_norms import plant_kind_of_object_type, setback_for
from shapely.geometry import Point, Polygon


class Violation(BaseModel):
    object_id: str
    object_type: str
    zone_id: str
    zone_type: str
    severity: str  # "forbidden"/"warning" (zone.severity) -- не все нарушения одинаково критичны
    distance_m: float
    required_m: float
    message: str


def find_violations(scene: Scene) -> list[Violation]:
    zones = []
    for zone in scene.restrictions:
        if zone.severity == "allowed" or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            zones.append((zone, poly))

    violations: list[Violation] = []
    for obj in scene.objects:
        kind = plant_kind_of_object_type(obj.type)
        if kind is None:
            continue
        point = Point(obj.position.x, obj.position.z)
        for zone, poly in zones:
            required = setback_for(zone.type, kind, zone.minDistance)
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
