"""
Общая логика для geojson_to_dxf.py и shp_to_dxf.py: обе читалки (GeoJSON и
Shapefile) сводят исходные данные к одному и тому же списку GeoJSON-подобных
Feature (dict с "geometry" и "properties") -- дальше их в DXF пишет один и тот
же код, независимо от исходного формата.

Точки -> POINT, линии -> LWPOLYLINE (открытая), полигоны -> LWPOLYLINE
(замкнутая; дырки, если есть, — отдельным замкнутым контуром на том же слое).
Слой берётся из указанного поля properties (--layer-field), иначе -- по типу
геометрии (IMPORTED_POINTS / IMPORTED_LINES / IMPORTED_POLYGONS).
"""

import re

import ezdxf
from pyproj import Transformer

DEFAULT_SRC_EPSG_GEOGRAPHIC = 4326  # WGS84 lon/lat -- типичный CRS для GeoJSON
DEFAULT_DST_EPSG = 32637            # UTM 37N, тот же CRS, что и в остальном проекте (Москва)


def sanitize_layer_name(name: str) -> str:
    name = str(name).strip().upper()
    name = re.sub(r"[^A-Z0-9_\-]", "_", name)
    return name or "IMPORTED"


def looks_geographic(features):
    """Эвристика: если все координаты по модулю укладываются в диапазон долгот/широт,
    считаем что это WGS84 (градусы), а не уже спроецированные метры."""
    for f in features:
        for pt in iter_coords(f.get("geometry")):
            x, y = pt[0], pt[1]
            if abs(x) > 180 or abs(y) > 90:
                return False
    return True


def iter_coords(geometry):
    """Плоский генератор всех (x, y[, z]) координат геометрии любого типа."""
    if geometry is None:
        return
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        yield coords
    elif gtype in ("MultiPoint", "LineString"):
        yield from coords
    elif gtype in ("MultiLineString", "Polygon"):
        for ring in coords:
            yield from ring
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                yield from ring


def build_transform(features, src_epsg, dst_epsg):
    if src_epsg is None:
        src_epsg = DEFAULT_SRC_EPSG_GEOGRAPHIC if looks_geographic(features) else dst_epsg
    if src_epsg == dst_epsg:
        return None, src_epsg
    return Transformer.from_crs(f"EPSG:{src_epsg}", f"EPSG:{dst_epsg}", always_xy=True), src_epsg


def pick_layer(properties, layer_field, fallback):
    if layer_field and properties:
        val = properties.get(layer_field)
        if val not in (None, ""):
            return sanitize_layer_name(val)
    return fallback


def write_features_to_dxf(features, out_path, layer_field=None, src_epsg=None, dst_epsg=DEFAULT_DST_EPSG):
    """features: список dict {"geometry": {...GeoJSON geometry...}, "properties": {...}}"""
    transformer, resolved_src_epsg = build_transform(features, src_epsg, dst_epsg)

    def tp(pt):
        x, y = pt[0], pt[1]
        if transformer is not None:
            x, y = transformer.transform(x, y)
        return (x, y)

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    seen_layers = set()

    def ensure_layer(name):
        if name not in seen_layers:
            doc.layers.add(name, color=7)
            seen_layers.add(name)

    n_points = n_lines = n_polys = 0

    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        gtype = geom["type"]
        coords = geom["coordinates"]

        if gtype in ("Point", "MultiPoint"):
            layer = pick_layer(props, layer_field, "IMPORTED_POINTS")
            ensure_layer(layer)
            pts = [coords] if gtype == "Point" else coords
            for pt in pts:
                x, y = tp(pt)
                msp.add_point((x, y, 0), dxfattribs={"layer": layer})
                n_points += 1

        elif gtype in ("LineString", "MultiLineString"):
            layer = pick_layer(props, layer_field, "IMPORTED_LINES")
            ensure_layer(layer)
            lines = [coords] if gtype == "LineString" else coords
            for line in lines:
                pts2d = [tp(pt) for pt in line]
                if len(pts2d) >= 2:
                    msp.add_lwpolyline(pts2d, dxfattribs={"layer": layer})
                    n_lines += 1

        elif gtype in ("Polygon", "MultiPolygon"):
            layer = pick_layer(props, layer_field, "IMPORTED_POLYGONS")
            ensure_layer(layer)
            polys = [coords] if gtype == "Polygon" else coords
            for poly in polys:
                for ring in poly:  # ring[0] = exterior, ring[1:] = holes
                    pts2d = [tp(pt) for pt in ring]
                    if len(pts2d) >= 3:
                        msp.add_lwpolyline(pts2d, close=True, dxfattribs={"layer": layer})
                        n_polys += 1

    doc.saveas(out_path)
    return {
        "points": n_points, "lines": n_lines, "polygons": n_polys,
        "layers": sorted(seen_layers), "src_epsg": resolved_src_epsg, "dst_epsg": dst_epsg,
    }
