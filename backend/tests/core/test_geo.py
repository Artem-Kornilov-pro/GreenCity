"""core/geo.py (issue #115): привязка сцены к карте для чертежей в МСК."""

import pytest
from pyproj import Transformer

from core.geo import MSK_MOSCOW, looks_like_msk, scene_geo

# Центры трёх объектов датасета (тайлы Мосгеотреста) и где эти улицы на карте.
DATASET = [
    ((-3852.92, 5888.17), (55.7199, 37.4369)),  # улица Багрицкого
    ((586.34, 14274.63), (55.7952, 37.5075)),  # Песчаный переулок
    ((3584.78, 10708.13), (55.7632, 37.5553)),  # улица Сергея Макеева
]


@pytest.mark.parametrize("origin,expected", DATASET)
def test_dataset_sites_land_on_their_streets(origin, expected):
    geo = scene_geo({"scale": 1.0, "origin": {"x": origin[0], "y": origin[1]}})
    assert geo["assumed"] and "Московская" in geo["crs"]
    assert geo["center"]["lat"] == pytest.approx(expected[0], abs=2e-4)
    assert geo["center"]["lon"] == pytest.approx(expected[1], abs=2e-4)


def test_affine_fit_is_within_a_metre_over_the_site():
    ox, oy = DATASET[0][0]
    geo = scene_geo({"scale": 1.0, "origin": {"x": ox, "y": oy}})
    a, b, c, d, e, f = geo["toScene"]
    to_wgs = Transformer.from_crs(MSK_MOSCOW, "EPSG:4326", always_xy=True)
    for dx, dz in [(0, 0), (900, -700), (-1500, 1500), (1900, 1900)]:
        lon, lat = to_wgs.transform(ox + dx, oy + dz)
        assert abs(a * lon + b * lat + c - dx) < 1.0
        assert abs(d * lon + e * lat + f - dz) < 1.0


def test_millimetre_drawing_is_converted_to_metres():
    ox, oy = DATASET[0][0]
    geo = scene_geo({"scale": 0.001, "origin": {"x": ox * 1000, "y": oy * 1000}})
    assert geo["center"]["lat"] == pytest.approx(DATASET[0][1][0], abs=2e-4)


@pytest.mark.parametrize(
    "x,y,msk",
    [(85, 65, False), (0, 0, False), (-3852, 5888, True), (586, 14274, True), (2_200_000, 480_000, False), (60_000, 0, False)],
)
def test_only_msk_like_coordinates_get_a_map(x, y, msk):
    # У самого нуля -- местная система (синтетика); миллионы -- МСК-50 или
    # другая зона; за 45 км -- не Москва.
    assert looks_like_msk(x, y) is msk
    assert (scene_geo({"scale": 1.0, "origin": {"x": x, "y": y}}) is not None) is msk
