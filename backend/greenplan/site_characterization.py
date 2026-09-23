"""
Признаки участка для Этапа 3 GreenPlan (issue #23, "Характеризация участка и
поиск похожих проектов"). Эта часть -- только характеризация: считаем то, что
уже можно посчитать по одной сцене, без обращения к корпусу готовых проектов.
Сам retrieval (поиск k ближайших среди 20 проектов) сюда не входит -- корпус
другой разработчик восстанавливает отдельно, и пока его нет, сравнивать не с
чем. Результат этого модуля -- будущий вектор признаков для того retrieval.

territory_type -- правила по составу и ПЛОЩАДИ зон + форме границы участка,
НЕ обученный классификатор (проверить точность на размеченном датасете
по-прежнему не на чем, см. issue #38). Раньше категорий было три
(двор/улица/неопределено) по трём правилам на голых СЧЁТЧИКАХ типов зон
(playground_zone>0 -> двор, иначе road>0 -> улица) -- это отбрасывало
"улицу" в пользу "двора" при единственной детской площадке на весь квартал
и не давало вообще никакого сигнала retrieval (issue #38:
pattern_retrieval.py включает one-hot territory_type в вектор только когда
territory_type_is_heuristic=False, а он был True всегда).

Теперь пять содержательных категорий вместо двух, и их граница -- официальная
8-категорийная классификация территорий из data/norms/assortment-msk/README.md
(матрица "вид растения x тип территории" из документа заказчика) -- берём из
неё только то, что различимо по геометрии сцены, а не по знанию "это школа"
или "это детская поликлиника":
- "двор" (дворовые территории) -- как и раньше, playground_zone плюс теперь
  требование building_count >= 1 (площадка без единого здания рядом -- это,
  вероятнее, часть парка, а не двор);
- "улица" (магистрали/проезды) -- есть зона road И граница участка вытянутая
  (длинная сторона минимального описанного прямоугольника хотя бы втрое
  длиннее короткой -- geometry.rect_sides, тот же приём, что и в
  placement_geometry.py::centerline_of);
- "площадь" (площади, общественно-деловые пространства) -- компактная (не
  вытянутая) заметная по площади территория почти без застройки и с низкой
  долей пригодной под озеленение площади (в основном мощение);
- "парк_сквер" (парки/бульвары/скверы/набережные/сады) -- совсем без зданий,
  заметная площадь и высокая доля пригодной под озеленение площади;
- "промышленная_охранная" (производственные/охранные/санитарные территории)
  -- без зданий, но с высокой долей площади под охранными зонами инженерных
  сетей (газ/канализация/водопровод/электричество вместе).
Правила проверяются в этом порядке (специфичное -- раньше общего, тот же
принцип, что и в parser/parse_dxf.py::POLYGON_RULES); первое совпавшее
побеждает. "неопределено" -- честный результат, когда ни одно правило не
сработало, а не подгонка под пятую категорию.

territory_type_is_heuristic=False выставляется для любой из пяти
содержательных категорий (правило сработало на объективных геометрических
сигналах: площадь конкретных типов зон, форма границы, число зданий) и
остаётся True только для "неопределено" -- ровно то отличие, которое
включает one-hot этого признака в pattern_retrieval.py (issue #38). Это
by-design компромисс, о котором просили: правила остаются правилами без
обучения на размеченных данных, но их достаточно, чтобы доверять результату
как факту для retrieval, а не прятать его за флагом "это просто догадка".
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.placement_geometry import rect_sides
from core.schemas import Scene
from core.setback_norms import setback_for

TerritoryType = Literal["двор", "улица", "площадь", "парк_сквер", "промышленная_охранная", "неопределено"]

# Доля площади участка под охранными зонами инженерных сетей, начиная с
# которой считаем территорию преимущественно промышленной/охранной, а не
# просто "участком с сетями" (сети есть почти везде).
INDUSTRIAL_ZONE_SHARE = 0.4
# Отношение длинной стороны минимального описанного прямоугольника к
# короткой, начиная с которого границу участка считаем "вытянутой" (типично
# для улицы/проезда, а не для двора/площади/парка).
STREET_ELONGATION_RATIO = 3.0
# Ниже этой площади геометрические признаки (форма, доля озеленения)
# статистически шумные -- крошечный обрезок участка не стоит классифицировать
# как площадь/парк только по счастливому совпадению долей.
MIN_AREA_FOR_OPEN_TYPES_SQM = 2000.0
PARK_PLANTABLE_RATIO = 0.7
SQUARE_MAX_PLANTABLE_RATIO = 0.35

ENGINEERING_ZONE_TYPES = ("gas_pipeline", "sewer", "water_pipeline", "electrical")


class SiteCharacteristics(BaseModel):
    total_area_sqm: float
    plantable_area_sqm: float
    plantable_ratio: float
    building_count: int
    restriction_zone_counts: dict[str, int]
    existing_tree_species: dict[str, int]
    territory_type: TerritoryType
    territory_type_is_heuristic: bool = True


def usable_planting_area(scene: Scene) -> BaseGeometry:
    """Грубая пригодная-под-озеленение площадь: явные allowed-зоны (или весь
    boundary, если их нет) минус forbidden/warning-зоны с отступом ДЛЯ ДЕРЕВА
    (setback_norms.setback_for) -- дерево требовательнее куста, так что оценка
    не завышена.

    Это НЕ то же самое, что allowed_area внутри generate_trees/generate_bushes
    в greenery_generator.py -- та ещё вычитает clearance вокруг уже
    расставленных объектов и различается по виду посадки (у куста отступы
    меньше). Здесь оценка одна и грубее: она нужна для характеризации участка
    и разбиения на зоны (см. zone_partitioning.py), а не для реальной
    расстановки, где точный отступ каждого вида посадки уже имеет значение.
    Если оба места разойдутся в подсчёте занятого места -- это ожидаемо, не
    баг.
    """
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return Polygon()

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return Polygon()

    allowed = [
        Polygon([(p.x, p.z) for p in zone.polygon])
        for zone in scene.restrictions
        if zone.severity == "allowed" and len(zone.polygon) >= 3
    ]
    allowed = [p for p in allowed if p.is_valid and p.area > 0]
    base = unary_union(allowed) if allowed else boundary_poly

    keep_out = []
    for zone in scene.restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid or poly.area == 0:
            continue
        setback = setback_for(zone.type, "tree", zone.minDistance)
        keep_out.append(poly.buffer(setback) if setback > 0 else poly)

    usable = base.intersection(boundary_poly)
    if keep_out:
        usable = usable.difference(unary_union(keep_out))
    return usable


def _boundary_elongation(boundary_poly: Polygon) -> float:
    """Отношение длинной стороны минимального описанного прямоугольника к
    короткой -- >=STREET_ELONGATION_RATIO означает вытянутую (уличную)
    форму границы. rect_sides переиспользован из placement.py (тот же приём,
    что и для "хребта" полосовых зон в centerline_of)."""
    sides = rect_sides(boundary_poly)
    lengths = sorted(side[2] for side in sides)
    short, long_ = lengths[0], lengths[-1]
    return long_ / short if short > 1e-6 else 1.0


def _guess_territory_type(
    zone_counts: dict[str, int],
    zone_areas: dict[str, float],
    building_count: int,
    total_area: float,
    plantable_ratio: float,
    elongation: float,
) -> tuple[TerritoryType, bool]:
    """(тип территории, is_heuristic). Порядок правил значим -- специфичное
    раньше общего, тот же принцип, что и в parser/parse_dxf.py::POLYGON_RULES.
    is_heuristic=False для любой из пяти содержательных категорий (сработал
    объективный геометрический сигнал), True -- только для "неопределено"
    (см. докстринг модуля про компромисс из issue #38)."""
    if zone_counts.get("playground_zone", 0) > 0 and building_count >= 1:
        return "двор", False

    industrial_share = sum(zone_areas.get(t, 0.0) for t in ENGINEERING_ZONE_TYPES) / total_area if total_area else 0.0
    if building_count == 0 and industrial_share >= INDUSTRIAL_ZONE_SHARE:
        return "промышленная_охранная", False

    if zone_counts.get("road", 0) > 0 and elongation >= STREET_ELONGATION_RATIO:
        return "улица", False

    if total_area >= MIN_AREA_FOR_OPEN_TYPES_SQM and building_count == 0:
        if plantable_ratio >= PARK_PLANTABLE_RATIO:
            return "парк_сквер", False
        if elongation < STREET_ELONGATION_RATIO and plantable_ratio <= SQUARE_MAX_PLANTABLE_RATIO:
            return "площадь", False

    return "неопределено", True


def characterize_site(scene: Scene, usable: Optional[BaseGeometry] = None) -> Optional[SiteCharacteristics]:
    """None, если у сцены нет валидной границы участка -- считать признаки
    не от чего, и это не ошибка вызывающего кода, а состояние сцены.

    usable -- проброс уже посчитанного usable_planting_area(scene)
    вызывающим кодом (deterministic_placement.generate_for_scene вызывает
    его сам для partition_zones): без этого параметра один и тот же
    дорогой unary_union по всем forbidden/warning-зонам участка считался бы
    в одном запросе дважды. Вызовы напрямую (тесты, pattern_corpus.py)
    как и раньше считают его сами."""
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return None

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return None

    total_area = boundary_poly.area
    if usable is None:
        usable = usable_planting_area(scene)
    plantable_area = 0.0 if usable.is_empty else usable.area

    zone_counts = dict(Counter(zone.type for zone in scene.restrictions))
    zone_areas: Counter[str] = Counter()
    for zone in scene.restrictions:
        if len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            zone_areas[zone.type] += poly.area

    species_counts: Counter[str] = Counter()
    for obj in scene.objects:
        if obj.type != "tree":
            continue
        species = obj.metadata.get("species")
        if species:
            species_counts[species] += 1

    plantable_ratio = round(plantable_area / total_area, 4) if total_area else 0.0
    building_count = scene.meta.buildingCount
    elongation = _boundary_elongation(boundary_poly)
    territory_type, is_heuristic = _guess_territory_type(
        zone_counts, dict(zone_areas), building_count, total_area, plantable_ratio, elongation
    )

    return SiteCharacteristics(
        total_area_sqm=round(total_area, 1),
        plantable_area_sqm=round(plantable_area, 1),
        plantable_ratio=plantable_ratio,
        building_count=building_count,
        restriction_zone_counts=zone_counts,
        existing_tree_species=dict(species_counts),
        territory_type=territory_type,
        territory_type_is_heuristic=is_heuristic,
    )
