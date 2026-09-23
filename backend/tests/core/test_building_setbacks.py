"""core.building_setbacks.compute_building_setbacks -- кольца отступов вокруг зданий
для отображения. Главное, ради чего расчёт переехал сюда с фронтенда: буфер
должен оставаться геометрически корректным на любых контурах, включая
невыпуклые и самопересекающиеся."""

import pytest
from shapely.geometry import Polygon

from core.building_setbacks import compute_building_setbacks
from core.setback_norms import SETBACK_NORMS

TREE_DISTANCE = SETBACK_NORMS["building"]["tree"]
BUSH_DISTANCE = SETBACK_NORMS["building"]["bush"]


def building(obj_id="b1", footprint=None, name="Дом 1"):
    if footprint is None:
        footprint = [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 10, "z": 10}, {"x": 0, "z": 10}]
    return {
        "id": obj_id,
        "type": "building",
        "metadata": {"footprint": footprint, "name": name},
    }


def as_polygon(zone):
    return Polygon([(p["x"], p["z"]) for p in zone["polygon"]])


def test_produces_tree_and_bush_ring_per_building():
    zones = compute_building_setbacks([building()])
    assert [z["type"] for z in zones] == ["building_setback_tree", "building_setback_bush"]
    assert [z["severity"] for z in zones] == ["warning", "forbidden"]
    assert [z["minDistance"] for z in zones] == [TREE_DISTANCE, BUSH_DISTANCE]
    assert all("Дом 1" in z["name"] and "Дом 1" in z["message"] for z in zones)


def test_rings_are_valid_and_grow_outward():
    source = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    tree_ring, bush_ring = (as_polygon(z) for z in compute_building_setbacks([building()]))

    assert tree_ring.is_valid and bush_ring.is_valid
    assert tree_ring.contains(source) and bush_ring.contains(source)
    assert tree_ring.contains(bush_ring)

    # Габариты проверяются, а не вписанность в точный буфер: на прямых участках
    # смещение точное, а вот дуги на углах приближены хордами (quad_segs=4),
    # поэтому сравнение с идеальной окружностью там расходится на сантиметры.
    sx0, sz0, sx1, sz1 = source.bounds
    for ring, distance in ((tree_ring, TREE_DISTANCE), (bush_ring, BUSH_DISTANCE)):
        rx0, rz0, rx1, rz1 = ring.bounds
        assert (rx0, rz0, rx1, rz1) == pytest.approx(
            (sx0 - distance, sz0 - distance, sx1 + distance, sz1 + distance), abs=1e-6
        )


def test_concave_footprint_stays_valid():
    # Г-образный контур: именно на таких прежний самописный обход вершин на
    # фронтенде давал самопересечение.
    l_shape = [
        {"x": 0, "z": 0}, {"x": 20, "z": 0}, {"x": 20, "z": 6},
        {"x": 6, "z": 6}, {"x": 6, "z": 20}, {"x": 0, "z": 20},
    ]
    zones = compute_building_setbacks([building(footprint=l_shape)])
    assert len(zones) == 2
    assert all(as_polygon(z).is_valid for z in zones)


def test_self_intersecting_footprint_does_not_crash():
    # "Бабочка" -- вырожденный контур из плохо экспортированного DXF.
    bowtie = [{"x": 0, "z": 0}, {"x": 10, "z": 10}, {"x": 10, "z": 0}, {"x": 0, "z": 10}]
    zones = compute_building_setbacks([building(footprint=bowtie)])
    assert all(as_polygon(z).is_valid for z in zones)


def test_ignores_non_buildings_and_footprintless_buildings():
    objects = [
        {"id": "t1", "type": "tree", "metadata": {"footprint": [{"x": 0, "z": 0}]}},
        {"id": "b2", "type": "building", "metadata": {}},
        {"id": "b3", "type": "building", "metadata": {"footprint": [{"x": 0, "z": 0}, {"x": 1, "z": 1}]}},
    ]
    assert compute_building_setbacks(objects) == []


def test_zone_ids_are_unique_per_building():
    zones = compute_building_setbacks([building("b1"), building("b2")])
    assert len({z["id"] for z in zones}) == len(zones) == 4
