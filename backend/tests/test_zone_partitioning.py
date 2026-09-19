"""zone_partitioning.partition_zones -- разбиение пригодной площади на зоны
по геометрическому типу (задел под Этап 4 GreenPlan, issue #23). Проверяется
на реальных тестовых участках -- та же причина, что в
test_site_characterization.py: собрать вручную Scene с содержательной
геометрией зданий/дорожек/границы дороже, чем взять готовые фикстуры."""

from schemas import Scene, SceneMeta
from site_characterization import usable_planting_area
from zone_partitioning import MIN_ZONE_AREA_SQM, partition_zones

ALL_KINDS = {"building_border", "path_corridor", "site_edge", "open_area"}


def _meta(**overrides):
    base = dict(scale=1.0, insunits=6, origin={"x": 0.0, "y": 0.0}, buildingCount=0, pointObjectCount=0)
    base.update(overrides)
    return SceneMeta(**base)


def test_returns_empty_without_boundary():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert partition_zones(scene) == []


def test_zone_kinds_are_known(location_scene):
    for scene in location_scene.values():
        for zone in partition_zones(scene):
            assert zone.kind in ALL_KINDS
            assert zone.area_sqm >= MIN_ZONE_AREA_SQM
            assert len(zone.polygon) >= 3


def test_zone_ids_unique_per_scene(location_scene):
    for scene in location_scene.values():
        zones = partition_zones(scene)
        assert len({z.id for z in zones}) == len(zones)


def test_building_border_present_when_buildings_exist(scene_02):
    # 02_courtyard_3buildings: 3 здания -- полоса вдоль них должна найтись.
    zones = partition_zones(scene_02)
    assert any(z.kind == "building_border" for z in zones)


def test_zones_do_not_double_count_area(scene_02):
    # Разбиение -- это партиция usable_planting_area: суммарная площадь зон не
    # может её превысить (с запасом на отброшенные мелкие обрезки меньше
    # MIN_ZONE_AREA_SQM на стыках полос).
    zones = partition_zones(scene_02)
    total_usable = usable_planting_area(scene_02).area
    total_zones = sum(z.area_sqm for z in zones)
    assert total_zones <= total_usable + 1e-6
    # Если зоны честно партиционируют площадь, а не только частично её
    # покрывают, потерять можно не больше, чем по одному MIN_ZONE_AREA_SQM
    # обрезку на каждую из 4 полос -- иначе где-то теряется целый кусок.
    assert total_usable - total_zones < 4 * MIN_ZONE_AREA_SQM


def test_site_edge_hugs_boundary(scene_06):
    # 06_courtyard_park_canvas: если site_edge вообще нашёлся, каждая его
    # зона должна лежать вплотную к внешней границе участка (в пределах
    # SITE_EDGE_BAND_M), а не быть куском где-то в середине.
    from shapely.geometry import LineString, Polygon
    from zone_partitioning import SITE_EDGE_BAND_M

    boundary_line = LineString([(p.x, p.z) for p in scene_06.boundary.polygon])
    edge_zones = [z for z in partition_zones(scene_06) if z.kind == "site_edge"]
    if not edge_zones:
        return  # на этом участке вся кромка могла уйти под building_border/path_corridor -- легитимно
    for zone in edge_zones:
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        assert boundary_line.distance(poly) < SITE_EDGE_BAND_M
