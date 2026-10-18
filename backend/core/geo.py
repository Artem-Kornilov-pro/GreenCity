"""
Привязка сцены к карте (issue #115). Чертежи Мосгеотреста и проектов по
ним -- в Московской городской системе координат (МСК): метры, оси на восток
и на север, начало -- около центра Москвы. Если координаты чертежа похожи на
МСК, сцена получает meta.geo: центр участка в широте и долготе и аффинное
преобразование «долгота, широта -> координаты сцены». По нему фронтенд кладёт
под сцену тайлы карты.

Привязка -- допущение («assumed»): система координат в DXF не записана.
Сверено на трёх объектах датасета: центр сцены попадает на свою улицу
(Багрицкого, Песчаный переулок, Макеева). Чертёж в другой системе (МСК-50,
местная система стройплощадки) по диапазону координат не проходит; если
всё же прошёл, подложка не совпадёт с чертежом -- это видно сразу.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import numpy as np
from pyproj import Transformer

# Московская городская система координат (параметры перехода к WGS 84 --
# общеупотребительные, точность -- метры, для подложки карты достаточно).
MSK_MOSCOW = (
    "+proj=tmerc +lat_0=55.66666666667 +lon_0=37.5 +k=1 +x_0=16.098 +y_0=14.512 +ellps=bessel "
    "+towgs84=316.151,78.924,589.65,-1.57273,2.69209,2.34693,8.4507 +units=m +no_defs"
)
MSK_LABEL = "Московская городская система координат (принято: в чертеже система не записана)"
# Москва в МСК -- в пределах ±45 км от начала. Чертёж с координатами у самого
# нуля -- скорее местная система (синтетика, стройплощадка), а не МСК.
_MSK_EXTENT_M = 45_000.0
_LOCAL_ORIGIN_M = 1_000.0
# Сколько вокруг центра покрывает подгонка преобразования, м.
_FIT_HALF_SIZE_M = 2_000.0


@lru_cache(maxsize=1)
def _to_wgs84() -> Transformer:
    return Transformer.from_crs(MSK_MOSCOW, "EPSG:4326", always_xy=True)


def looks_like_msk(x_m: float, y_m: float) -> bool:
    """Координаты начала сцены в метрах похожи на МСК."""
    inside = abs(x_m) <= _MSK_EXTENT_M and abs(y_m) <= _MSK_EXTENT_M
    return inside and max(abs(x_m), abs(y_m)) >= _LOCAL_ORIGIN_M


def scene_geo(meta: dict) -> Optional[dict]:
    """meta.geo для сцены или None: {crs, assumed, center: {lat, lon},
    toScene: [a, b, c, d, e, f]} -- x = a*lon + b*lat + c, z = d*lon + e*lat + f
    (x, z -- метры сцены)."""
    scale = float(meta.get("scale") or 1.0)
    origin = meta.get("origin") or {}
    ox, oy = float(origin.get("x", 0.0)) * scale, float(origin.get("y", 0.0)) * scale
    if not looks_like_msk(ox, oy):
        return None
    # Точки сцены по сетке вокруг центра -> долгота и широта; по ним --
    # аффинное преобразование обратно (на 4 км проекция почти линейна).
    grid = np.linspace(-_FIT_HALF_SIZE_M, _FIT_HALF_SIZE_M, 5)
    xs, zs = np.meshgrid(grid, grid)
    xs, zs = xs.ravel(), zs.ravel()
    lon, lat = _to_wgs84().transform(ox + xs, oy + zs)
    lon, lat = np.asarray(lon), np.asarray(lat)
    design = np.column_stack([lon, lat, np.ones_like(lon)])
    to_x, *_ = np.linalg.lstsq(design, xs, rcond=None)
    to_z, *_ = np.linalg.lstsq(design, zs, rcond=None)
    center_lon, center_lat = _to_wgs84().transform(ox, oy)
    return {
        "crs": MSK_LABEL,
        "assumed": True,
        "center": {"lat": round(float(center_lat), 7), "lon": round(float(center_lon), 7)},
        "toScene": [float(v) for v in (*to_x, *to_z)],
    }
