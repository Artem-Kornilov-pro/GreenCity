"""
Признаки участка для Этапа 3 GreenPlan (issue #23, "Характеризация участка и
поиск похожих проектов"). Эта часть -- только характеризация: считаем то, что
уже можно посчитать по одной сцене, без обращения к корпусу готовых проектов.
Сам retrieval (поиск k ближайших среди 20 проектов) сюда не входит -- корпус
другой разработчик восстанавливает отдельно, и пока его нет, сравнивать не с
чем. Результат этого модуля -- будущий вектор признаков для того retrieval.

territory_type -- ЭВРИСТИКА по составу зон и объектов сцены, НЕ обученный
классификатор: проверить точность не на чем, ни одного реального проекта с
подтверждённым типом территории («это точно двор», «это точно улица») у нас
пока нет, только шесть синтетических тестовых участков в
locations/location_old/. Поэтому:
- правил три, а не восемь категорий из data/norms/assortment-msk/ -- строить
  восьмикатегорийный классификатор не на чем проверить, а «неопределено» --
  честный результат, когда сигналов недостаточно;
- у результата есть флаг territory_type_is_heuristic, чтобы будущий Этап 4
  (фильтр паттернов по типу территории) не принял догадку за факт без
  разметки человеком.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel
from schemas import Scene
from setback_norms import setback_for
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

TerritoryType = Literal["двор", "улица", "неопределено"]


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


def _guess_territory_type(zone_counts: dict[str, int]) -> TerritoryType:
    """Порядок проверок важен: "двор" смотрим раньше "улицы", потому что
    двор с площадкой у проезжей части (обычный случай -- благоустройство
    вдоль улицы почти всегда включает дворовые площадки) не должен
    перевесить очевидный сигнал "здесь есть площадка для детей" в пользу
    "здесь просто улица". "road" (проезжая часть, не путать с
    pedestrian_path -- пешеходной дорожкой) сейчас встречается только на
    одном из шести тестовых участков (locations/location_old/05_klykova_avenue),
    но там же есть и playground_zone -- то есть по этому правилу участок
    классифицируется как "двор", а не "улица": чистого положительного
    примера "улица без единой площадки" в тестовых данных нет, эта ветка
    правила пока не проверена на реальном участке.
    """
    if zone_counts.get("playground_zone", 0) > 0:
        return "двор"
    if zone_counts.get("road", 0) > 0:
        return "улица"
    return "неопределено"


def characterize_site(scene: Scene) -> Optional[SiteCharacteristics]:
    """None, если у сцены нет валидной границы участка -- считать признаки
    не от чего, и это не ошибка вызывающего кода, а состояние сцены."""
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return None

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return None

    total_area = boundary_poly.area
    usable = usable_planting_area(scene)
    plantable_area = 0.0 if usable.is_empty else usable.area

    zone_counts = dict(Counter(zone.type for zone in scene.restrictions))

    species_counts: Counter[str] = Counter()
    for obj in scene.objects:
        if obj.type != "tree":
            continue
        species = obj.metadata.get("species")
        if species:
            species_counts[species] += 1

    return SiteCharacteristics(
        total_area_sqm=round(total_area, 1),
        plantable_area_sqm=round(plantable_area, 1),
        plantable_ratio=round(plantable_area / total_area, 4) if total_area else 0.0,
        building_count=scene.meta.buildingCount,
        restriction_zone_counts=zone_counts,
        existing_tree_species=dict(species_counts),
        territory_type=_guess_territory_type(zone_counts),
    )
