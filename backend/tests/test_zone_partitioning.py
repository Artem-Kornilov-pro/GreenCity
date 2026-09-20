"""zone_partitioning.partition_zones -- разбиение пригодной площади на зоны
по геометрическому типу (задел под Этап 4 GreenPlan, issue #23). Проверяется
на реальных тестовых участках -- та же причина, что в
test_site_characterization.py: собрать вручную Scene с содержательной
геометрией зданий/дорожек/границы дороже, чем взять готовые фикстуры."""

import pytest

from schemas import Point2, Scene, SceneMeta
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
    # Каждый z.area_sqm округлён до 2 знаков (round(poly.area, 2) в _emit),
    # поэтому сумма округлённых площадей может превысить точную сумму не
    # больше чем на 0.005 на зону -- 1e-6 этого не учитывал и был слишком
    # тесен для сцен с несколькими десятками зон.
    rounding_budget = 0.005 * len(zones) + 1e-9
    assert total_zones <= total_usable + rounding_budget
    # Если зоны честно партиционируют площадь, а не только частично её
    # покрывают, потерять можно не больше, чем по одному MIN_ZONE_AREA_SQM
    # обрезку на каждую из 4 полос -- иначе где-то теряется целый кусок.
    assert total_usable - total_zones < 4 * MIN_ZONE_AREA_SQM


def test_site_edge_ring_has_no_hole_leak_on_compact_site():
    # Круглый участок (не узкая вытянутая полоса, как реальные локации в
    # фикстурах) -- boundary.buffer(-SITE_EDGE_BAND_M) не пуст, значит
    # site_edge обязан быть КОЛЬЦОМ (Polygon с дыркой на уровне геометрии
    # difference), а не сплошным диском. Регрессия на баг из _emit():
    # раньше interior ring отбрасывался (брались только .exterior.coords),
    # и реконструированный Polygon(zone.polygon) получался сплошным,
    # перекрывая open_area. Дырка теперь схлопывается разрезом на два
    # простых куска без дыр (_split_out_hole) -- поэтому site_edge на таком
    # участке ожидаемо приходит НЕСКОЛЬКИМИ GeometricZone, а не одной.
    import math

    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    from zone_partitioning import SITE_EDGE_BAND_M

    from schemas import Boundary

    radius = 18.0
    n = 48
    circle = [
        Point2(x=radius * math.cos(2 * math.pi * k / n), z=radius * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]
    scene = Scene(boundary=Boundary(polygon=circle, sourceLayer="TERRITORY_BOUNDARY"), restrictions=[], objects=[], meta=_meta())

    zones = partition_zones(scene)
    edge_zones = [z for z in zones if z.kind == "site_edge"]
    open_zones = [z for z in zones if z.kind == "open_area"]
    assert edge_zones and open_zones
    assert len(edge_zones) > 1  # дырка схлопнута разрезом, не одним куском

    edge_union = unary_union([Polygon([(p.x, p.z) for p in z.polygon]) for z in edge_zones])
    open_union = unary_union([Polygon([(p.x, p.z) for p in z.polygon]) for z in open_zones])
    assert all(p.is_valid for p in [edge_union, open_union] if p.geom_type != "GeometryCollection")

    # Реконструированная площадь честного кольца обязана совпадать с
    # заявленной суммой area_sqm (площадь круга минус дырка) -- если бы
    # дырка терялась, реконструированная площадь была бы заметно больше.
    assert edge_union.area == pytest.approx(sum(z.area_sqm for z in edge_zones), abs=0.5)

    # Центр круга -- вглубине open_area, за пределами site_edge (band=3м от
    # границы) -- при потерянной дырке центр ошибочно тоже попадал бы в
    # "сплошной" site_edge.
    assert not edge_union.contains(Point(0.0, 0.0))
    assert open_union.contains(Point(0.0, 0.0))
    assert edge_union.intersection(open_union).area < 1.0

    # Каждая точка на кольце ровно посередине полосы (radius - band/2)
    # обязана лежать в site_edge, а не потеряться на стыке разреза.
    for k in range(24):
        angle = 2 * math.pi * k / 24
        mid_r = radius - SITE_EDGE_BAND_M / 2
        p = Point(mid_r * math.cos(angle), mid_r * math.sin(angle))
        assert edge_union.contains(p), f"точка на кольце потеряна на стыке разреза: {p}"


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
