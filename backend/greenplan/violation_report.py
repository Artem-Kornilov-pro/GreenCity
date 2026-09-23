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
tests/greenplan/test_deterministic_placement.py::_independent_zone_violations -- здесь
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

Индексация через STRtree -- тот же приём и та же причина, что уже описаны в
greenery_generator.py::_ZoneIndex ("при тысячах точек и десятках тысяч зон
линейный перебор был главной причиной, почему generate-greenery не
укладывался в разумное время"): наивный перебор всех зон на каждый объект
на крупном реальном участке (20_makeeva_s, 88 зданий) занимал ~13.5 секунды
сам по себе, без единого обращения к LLM. В отличие от _ZoneIndex (там
`.nearest()` -- одна ближайшая зона для текстового объяснения), здесь нужны
`.query()` -- ВСЕ зоны в пределах охвата, потому что объект может нарушать
несколько разных зон одновременно, и каждую нужно честно проверить своим
нормативом. Индекс строится по зонам, буферизованным на ХУДШИЙ ИЗ ДВУХ
видов посадки отступ (используется только для грубого отбора кандидатов по
bounding box) -- точное расстояние и точная норма для конкретного объекта
считаются уже после, на исходной (небуферизованной) геометрии зоны, поэтому
результат совпадает с прежним наивным перебором бит-в-бит, только быстрее.
"""

from __future__ import annotations

from pydantic import BaseModel
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from core.schemas import Scene
from core.setback_norms import SPECIES_SETBACK_RULES, plant_kind_of_object_type, setback_for


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
    """(STRtree по буферизованным зонам, параллельный список (zone, исходный
    полигон)) -- или (None, []), если проверять нечего. Буфер каждой зоны --
    худший случай среди отступа для дерева, для куста и всех правил по
    породе для этого типа зоны (setback_norms.SPECIES_SETBACK_RULES: липе
    10 м от здания), чтобы точный отбор кандидатов ниже не потерял ни одного
    реального нарушения, каким бы ни был вид конкретного объекта."""
    entries: list[tuple] = []
    buffered: list[Polygon] = []
    for zone in scene.restrictions:
        if zone.severity == "allowed" or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid or poly.area == 0:
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
