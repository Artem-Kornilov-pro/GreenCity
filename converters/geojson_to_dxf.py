#!/usr/bin/env python3
"""
GeoJSON -> DXF (для данных вида data.mos.ru: улицы, здания, границы участков и т.д.,
и в целом любой GeoJSON FeatureCollection).

Точки -> POINT, линии -> LWPOLYLINE, полигоны -> LWPOLYLINE (замкнутая).
Слой берётся из указанного поля properties, иначе -- по типу геометрии.

Координаты по умолчанию считаются WGS84 (lon/lat), если не выглядят уже
метрическими (эвристика: |x|<=180 и |y|<=90 -> градусы) -- перепроецируются в
EPSG:32637 (UTM 37N), тот же CRS, что и в остальном проекте (Москва). Другой
исходный/целевой CRS -- через --src-epsg/--dst-epsg.

Использование:
    python3 geojson_to_dxf.py input.geojson output.dxf
    python3 geojson_to_dxf.py input.geojson output.dxf --layer-field type
    python3 geojson_to_dxf.py input.geojson output.dxf --src-epsg 4326 --dst-epsg 32637
"""

import argparse
import json
import sys

from geo_to_dxf_core import write_features_to_dxf, DEFAULT_DST_EPSG


def load_features(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        return data["features"]
    if data.get("type") == "Feature":
        return [data]
    if data.get("type") in ("Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"):
        return [{"type": "Feature", "geometry": data, "properties": {}}]
    sys.exit("Не похоже на GeoJSON: нет верхнеуровневого 'type' FeatureCollection/Feature/геометрии")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Путь к .geojson/.json")
    ap.add_argument("output", help="Путь к выходному .dxf")
    ap.add_argument("--layer-field", default=None, help="Поле properties для имени слоя (напр. 'type', 'layer')")
    ap.add_argument("--src-epsg", type=int, default=None, help="EPSG исходных координат (по умолчанию -- автоопределение)")
    ap.add_argument("--dst-epsg", type=int, default=DEFAULT_DST_EPSG, help=f"EPSG для DXF (по умолчанию {DEFAULT_DST_EPSG} -- UTM 37N)")
    args = ap.parse_args()

    features = load_features(args.input)
    if not features:
        sys.exit("В файле нет объектов (features)")

    stats = write_features_to_dxf(
        features, args.output,
        layer_field=args.layer_field, src_epsg=args.src_epsg, dst_epsg=args.dst_epsg,
    )
    print(f"Прочитано объектов: {len(features)}")
    print(f"CRS: EPSG:{stats['src_epsg']} -> EPSG:{stats['dst_epsg']}")
    print(f"Записано: {stats['points']} точек, {stats['lines']} линий, {stats['polygons']} полигонов")
    print(f"Слои: {', '.join(stats['layers']) or '(нет)'}")
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
