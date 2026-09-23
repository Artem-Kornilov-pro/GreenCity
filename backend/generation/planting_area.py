"""
Где генератору по сетке (greenery_generator.py) можно сажать: зоны, от
которых отступаем (с отступом по виду посадки), разрешённые зоны озеленения,
занятые места у существующих объектов, и пространственный индекс зон для
текстового объяснения "почему здесь" у каждой точки.
"""

from __future__ import annotations

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from core.schemas import RestrictionZone, SceneObject
from core.setback_norms import PlantKind, SpeciesArg, setback_for

# Небольшой запретный радиус вокруг уже существующих непостроечных объектов
# (лавочки, фонари, вручную расставленные деревья/кусты и т.п.), чтобы
# генератор не сажал дерево вплотную к ним. Здания сюда не входят -- для них
# уже есть отдельная зона restrictions с нормальным отступом по СНиП.
EXISTING_OBJECT_CLEARANCE_M = 1.5


def _keep_out_shapes(
    restrictions: list[RestrictionZone], plant_kind: PlantKind, species: SpeciesArg = None
) -> list[Polygon]:
    """Полигоны зон, куда автогенератор не должен сажать растения данного
    вида (дерево или кустарник), каждый расширен наружу на положенный ЭТОМУ
    виду отступ -- для кустарника отступы в setback_norms.SETBACK_NORMS
    меньше, чем для дерева (п.17 ТЗ: "кустарники -- меньшие расстояния").

    Сюда идут и severity == "forbidden", И severity == "warning" (парковка,
    пешеходные дорожки, наземная ЛЭП и т.п.). Это осознанное расхождение с
    буквальным текстом исходного докстринга в main.py (там был только
    forbidden) -- у warning-зон есть отдельный смысл: они специально
    оставлены "предупреждение, а не запрет" для интерактивного
    перетаскивания на презентации (ТЗ п.8: пользователь может руками
    перетащить дерево на парковку/дорожку и увидеть жёлтое предупреждение).
    Но для АВТОгенератора это же самое "можно, но не нужно" означает, что
    сажать он туда не должен -- иначе на сгенерированной сцене деревья сразу
    стоят посреди парковки, что для демонстрации выглядит как баг, а не
    фича. Ручной drag&drop (frontend/src/geometry.ts::checkViolations) эту
    функцию не использует и по-прежнему трактует warning как
    "разрешено с предупреждением" -- поведение интерактивной проверки не
    меняется, меняется только то, что генератор сам туда не полезет.

    species -- все виды, которые могут встать на эту площадь; отступ от
    каждой зоны -- наибольший среди них (setback_norms.setback_for).
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid or poly.area == 0:
            continue
        setback = setback_for(zone.type, plant_kind, zone.minDistance, species=species)
        shapes.append(poly.buffer(setback) if setback > 0 else poly)
    return shapes


def _raw_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """То же самое, что и _keep_out_shapes, но БЕЗ отступа по виду посадки --
    для газона (setback_kind=None в каталоге, см. plant_catalog.py): у голого
    дёрна нет ни корней, ни кроны, поэтому норм в setback_norms.py для него
    нет, и достаточно не залезать на саму охранную зону (она уже отбуферена
    на minDistance сети самим парсером, см. parser/parse_dxf.py)."""
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            shapes.append(poly)
    return shapes


def _planting_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """Полигоны явно допустимых зон (severity == "allowed", например газон
    из слоя GRASS/LAWN -- см. parser/parse_dxf.py POLYGON_RULES). Если такие
    зоны в сцене размечены, генератор сажает только внутри них (плюс с
    учётом _keep_out_shapes) -- это и осмысленнее (дерево должно расти в
    зоне озеленения, а не просто "где угодно, где не запрещено"), и решает
    проблему избыточной плотности: вместо всей площади участка кандидатные
    точки ищутся только по факту размеченным под озеленение местам.
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity != "allowed" or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            shapes.append(poly)
    return shapes


def _existing_object_shapes(objects: list[SceneObject], clearance: float = EXISTING_OBJECT_CLEARANCE_M) -> list[Polygon]:
    """Небольшой круг-буфер вокруг каждого не-здания -- не даём генератору
    поставить новый объект поверх уже существующего. Здания намеренно
    исключены -- они уже покрыты своей zone type="building" в restrictions.

    clearance -- меньше для кустов/газона, чем для деревьев по умолчанию
    (EXISTING_OBJECT_CLEARANCE_M): дерево не должно стоять вплотную к лавке,
    а вот куст или плитка газона рядом с ней -- обычное дело.
    """
    return [
        Point(obj.position.x, obj.position.z).buffer(clearance)
        for obj in objects
        if obj.type != "building"
    ]


class _ZoneIndex:
    """Полигоны зон ограничений + STRtree для _placement_reason, построенные
    ОДИН РАЗ на вызов generate_trees/generate_bushes/generate_lawn, а не на
    каждую принятую точку. Раньше _placement_reason пересобирала Polygon для
    ВСЕХ зон заново на каждый вызов -- при тысячах принятых деревьев и
    десятках тысяч зон (плотные реальные данные, не тестовые локации) это
    было на порядок дороже, чем сами keep_out/allowed_area вычисления, и
    было главной причиной, почему generate-greenery не укладывался в разумное
    время. STRtree.nearest() -- O(log N) вместо линейного перебора всех зон
    на каждую точку."""

    def __init__(self, restrictions: list[RestrictionZone]):
        self.polys: list[Polygon] = []
        self.zones: list[RestrictionZone] = []
        for zone in restrictions:
            if len(zone.polygon) < 3:
                continue
            poly = Polygon([(p.x, p.z) for p in zone.polygon])
            if not poly.is_valid:
                continue
            self.polys.append(poly)
            self.zones.append(zone)
        self.tree = STRtree(self.polys) if self.polys else None


def _placement_reason(x: float, z: float, zone_index: _ZoneIndex) -> list[str]:
    """Человекочитаемое объяснение размещения для metadata.reason -- формат
    из ТЗ п.17 (пример: "внутри зоны озеленения", "4.2 м до водопровода").
    """
    reasons = ["внутри допустимой зоны озеленения"]
    if zone_index.tree is not None:
        pt = Point(x, z)
        idx = int(zone_index.tree.nearest(pt))
        poly = zone_index.polys[idx]
        zone = zone_index.zones[idx]
        distance = 0.0 if poly.contains(pt) else poly.exterior.distance(pt)
        reasons.append(f"{distance:.1f} м до ближайшего ограничения ({zone.name})")
    return reasons
