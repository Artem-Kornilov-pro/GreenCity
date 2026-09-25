"""greenplan.site_characterization.characterize_site -- признаки участка для будущего
Этапа 3 GreenPlan (retrieval). Сверяется на реальных тестовых участках
(locations/location_old/), т.к. синтетических Scene с валидной геометрией
границ вручную не построить дёшево -- только эти шесть у нас и есть."""

import pytest
from shapely.geometry import Polygon

from core.schemas import Boundary, Point2, RestrictionZone, Scene, SceneMeta
from greenplan.site_characterization import characterize_site, usable_planting_area


def _meta(**overrides):
    base = dict(scale=1.0, insunits=6, origin={"x": 0.0, "y": 0.0}, buildingCount=0, pointObjectCount=0)
    base.update(overrides)
    return SceneMeta(**base)


def test_returns_none_without_boundary():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert characterize_site(scene) is None


def test_area_and_ratio_are_consistent(location_scene):
    for scene in location_scene.values():
        result = characterize_site(scene)
        assert result is not None
        assert result.total_area_sqm > 0
        assert 0.0 <= result.plantable_area_sqm <= result.total_area_sqm
        assert result.plantable_ratio == round(result.plantable_area_sqm / result.total_area_sqm, 4)


def test_building_count_matches_scene_meta(location_scene):
    for scene in location_scene.values():
        result = characterize_site(scene)
        assert result.building_count == scene.meta.buildingCount


def test_restriction_zone_counts_sum_to_all_restrictions(scene_02):
    result = characterize_site(scene_02)
    assert sum(result.restriction_zone_counts.values()) == len(scene_02.restrictions)


def test_no_existing_trees_on_synthetic_locations(location_scene):
    # Синтетические тестовые участки не содержат объектов type="tree" -- это
    # факт о фикстурах, а не про generate_trees (тот их и генерирует).
    for scene in location_scene.values():
        assert characterize_site(scene).existing_tree_species == {}


def test_territory_type_guess_is_playground_before_road(location_scene):
    # 03_lshape_vs_rect: есть playground_zone и здания -- должен распознаться
    # как двор, даже при наличии дорожек (pedestrian_path, не road).
    result = characterize_site(location_scene[3])
    assert result.territory_type == "двор"
    # Issue #38: содержательная категория -- сработал объективный сигнал
    # (playground_zone + здания), а не догадка "по умолчанию".
    assert result.territory_type_is_heuristic is False


def test_territory_type_guess_road_needs_elongated_boundary():
    # Компактная (не вытянутая) граница с дорогой -- это не "улица" (для нашей
    # эвристики улица должна быть вытянутой геометрически, не просто
    # содержать любую road-зону), а "площадь": здание отсутствует, участок
    # почти весь занят проезжей частью (низкая доля озеленяемой площади).
    square = [Point2(x=0, z=0), Point2(x=50, z=0), Point2(x=50, z=50), Point2(x=0, z=50)]
    road = RestrictionZone(
        id="r1", type="road", name="Проезжая часть", polygon=square, severity="forbidden", minDistance=2.0, message=""
    )
    scene = Scene(boundary=Boundary(polygon=square, sourceLayer="BOUNDARY"), restrictions=[road], objects=[], meta=_meta())
    result = characterize_site(scene)
    assert result.territory_type == "площадь"
    assert result.territory_type_is_heuristic is False


def test_territory_type_guess_road_with_elongated_boundary_is_ulitsa():
    # Тот же принцип, но граница вытянута (200x20, отношение сторон 10) --
    # длинная узкая полоса с дорогой это и есть "улица".
    strip = [Point2(x=0, z=0), Point2(x=200, z=0), Point2(x=200, z=20), Point2(x=0, z=20)]
    road = RestrictionZone(
        id="r1", type="road", name="Проезжая часть", polygon=strip, severity="forbidden", minDistance=2.0, message=""
    )
    scene = Scene(boundary=Boundary(polygon=strip, sourceLayer="BOUNDARY"), restrictions=[road], objects=[], meta=_meta())
    result = characterize_site(scene)
    assert result.territory_type == "улица"
    assert result.territory_type_is_heuristic is False


def test_territory_type_guess_road_and_playground_together_is_dvor(location_scene):
    # 05_klykova_avenue -- единственный тестовый участок с type="road", но у
    # него же есть playground_zone и здания: по документированному приоритету
    # это "двор", не "улица" (см. докстринг _guess_territory_type).
    result = characterize_site(location_scene[5])
    assert result.territory_type == "двор"


def test_territory_type_guess_park_needs_no_buildings_and_high_plantable_ratio():
    # Большой (>= MIN_AREA_FOR_OPEN_TYPES_SQM) участок без зданий и почти
    # целиком пригодный под озеленение (allowed-зона на весь контур) --
    # парк/сквер, не "неопределено".
    square = [Point2(x=0, z=0), Point2(x=100, z=0), Point2(x=100, z=100), Point2(x=0, z=100)]
    grass = RestrictionZone(
        id="g1", type="protected_zone", name="Газон", polygon=square, severity="allowed", minDistance=0.0, message=""
    )
    scene = Scene(boundary=Boundary(polygon=square, sourceLayer="BOUNDARY"), restrictions=[grass], objects=[], meta=_meta())
    result = characterize_site(scene)
    assert result.territory_type == "парк_сквер"
    assert result.territory_type_is_heuristic is False


def test_territory_type_guess_industrial_needs_dominant_engineering_zones():
    # Большая доля площади под охранными зонами инженерных сетей и ни одного
    # здания -- промышленная/охранная территория, а не "неопределено".
    square = [Point2(x=0, z=0), Point2(x=100, z=0), Point2(x=100, z=100), Point2(x=0, z=100)]
    gas = RestrictionZone(
        id="e1", type="gas_pipeline", name="Газопровод", polygon=square, severity="forbidden", minDistance=2.0, message=""
    )
    scene = Scene(boundary=Boundary(polygon=square, sourceLayer="BOUNDARY"), restrictions=[gas], objects=[], meta=_meta())
    result = characterize_site(scene)
    assert result.territory_type == "промышленная_охранная"
    assert result.territory_type_is_heuristic is False


def test_territory_type_guess_undetermined_without_signals(scene_02):
    # 02_courtyard_3buildings: ни playground_zone, ни road, здания есть --
    # ни одно правило не срабатывает, честное "неопределено" остаётся
    # эвристикой (единственный случай is_heuristic=True).
    result = characterize_site(scene_02)
    assert result.territory_type == "неопределено"
    assert result.territory_type_is_heuristic is True


def test_usable_planting_area_empty_without_boundary():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert usable_planting_area(scene).is_empty


def test_usable_area_repairs_self_intersecting_zones_instead_of_dropping_them():
    # 20_makeeva_s: невалидные контуры разрешённой земли выбрасывались, и
    # пригодная площадь была 2,5 тыс. м² вместо 67 тыс. -- GreenPlan не
    # сажал ни одного дерева.
    from helpers import make_scene, make_zone

    def bowtie(x0):
        return [Point2(x=x0, z=0), Point2(x=x0 + 20, z=20), Point2(x=x0 + 20, z=0), Point2(x=x0, z=20)]

    allowed = make_zone(id="a", type="protected_zone", name="GROUND", severity="allowed", polygon=bowtie(-40))
    forbidden = make_zone(id="f", type="building", polygon=bowtie(10))
    usable = usable_planting_area(make_scene(restrictions=[allowed, forbidden]))
    assert usable.area == pytest.approx(200, rel=0.01)  # две половинки "бабочки" по 100 м²
    assert usable.intersection(Polygon([(10, 0), (30, 0), (30, 20), (10, 20)])).area == 0
