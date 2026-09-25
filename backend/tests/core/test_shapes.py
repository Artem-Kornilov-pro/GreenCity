"""core/shapes.py -- полигон из контура сцены: невалидный чинится, а не
отбрасывается."""

import pytest

from core.schemas import Point2
from core.shapes import polygon_from_points

# "Бабочка": самопересекающийся контур -- обычное дело в реальных DXF.
BOWTIE = [Point2(x=0, z=0), Point2(x=10, z=10), Point2(x=10, z=0), Point2(x=0, z=10)]


def test_valid_contour_is_kept_as_is():
    square = [Point2(x=0, z=0), Point2(x=4, z=0), Point2(x=4, z=4), Point2(x=0, z=4)]
    assert polygon_from_points(square).area == pytest.approx(16)


def test_self_intersecting_contour_is_repaired_not_dropped():
    geom = polygon_from_points(BOWTIE)
    assert geom is not None and geom.is_valid
    assert geom.area == pytest.approx(50)


def test_single_keeps_the_largest_part():
    geom = polygon_from_points(BOWTIE, single=True)
    assert geom.geom_type == "Polygon"
    assert geom.area == pytest.approx(25)


@pytest.mark.parametrize("points", [[], [Point2(x=0, z=0), Point2(x=1, z=0)], [Point2(x=0, z=0), Point2(x=1, z=0), Point2(x=2, z=0)]])
def test_contour_without_area_gives_none(points):
    assert polygon_from_points(points) is None
