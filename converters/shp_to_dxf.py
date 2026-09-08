#!/usr/bin/env python3
"""
Shapefile (.shp) -> DXF (для обезличенных подоснов АО «Мосгеотрест» и подобных
данных). Использует pyshp -- чистый Python, без GDAL.

Читает .shp (+ соответствующие .dbf/.shx рядом), каждую запись превращает в
GeoJSON-геометрию через shape.__geo_interface__ и дальше пишет тем же кодом,
что и geojson_to_dxf.py: точки -> POINT, линии -> LWPOLYLINE, полигоны ->
LWPOLYLINE (замкнутая). Слой -- из указанного поля атрибутов (--layer-field),
иначе -- по типу геометрии.

Shapefile-данные почти всегда уже в метрической проекции (не WGS84) -- если
знаете её EPSG, укажите --src-epsg явно, иначе координаты будут приняты как
уже метрические (без перепроецирования), кроме случая, когда они явно похожи
на градусы (тогда сработает автоопределение, как в geojson_to_dxf.py).

Использование:
    python3 shp_to_dxf.py input.shp output.dxf
    python3 shp_to_dxf.py input.shp output.dxf --layer-field NAME --src-epsg 3857
"""

import argparse
import sys

import shapefile  # pyshp

from geo_to_dxf_core import write_features_to_dxf, DEFAULT_DST_EPSG


def load_features(path):
    reader = shapefile.Reader(path)
    features = []
    for sr in reader.shapeRecords():
        geom = sr.shape.__geo_interface__
        if geom is None:
            continue
        props = sr.record.as_dict()
        features.append({"geometry": geom, "properties": props})
    return features


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Путь к .shp (файлы .dbf/.shx должны лежать рядом)")
    ap.add_argument("output", help="Путь к выходному .dxf")
    ap.add_argument("--layer-field", default=None, help="Поле атрибутов для имени слоя")
    ap.add_argument("--src-epsg", type=int, default=None, help="EPSG исходных координат (по умолчанию -- автоопределение/без перепроецирования)")
    ap.add_argument("--dst-epsg", type=int, default=DEFAULT_DST_EPSG, help=f"EPSG для DXF (по умолчанию {DEFAULT_DST_EPSG} -- UTM 37N)")
    args = ap.parse_args()

    try:
        features = load_features(args.input)
    except shapefile.ShapefileException as e:
        sys.exit(f"Не удалось прочитать shapefile: {e}")

    if not features:
        sys.exit("В shapefile нет объектов")

    stats = write_features_to_dxf(
        features, args.output,
        layer_field=args.layer_field, src_epsg=args.src_epsg, dst_epsg=args.dst_epsg,
    )
    print(f"Прочитано объектов: {len(features)}")
    print(f"CRS: EPSG:{stats['src_epsg']} -> EPSG:{stats['dst_epsg']}")
    print(f"Записано: {stats['points']} точек, {stats['lines']} линий, {stats['polygons']} полигонов")
    print(f"Слои: {', '.join(stats['layers']) or '(нет)'}")
    print(f"-> {args.output}")

    if args.src_epsg is None:
        print("\nПодсказка: если геометрия в DXF выглядит смещённой/неверного масштаба -- "
              "укажите реальный EPSG исходных данных через --src-epsg (для подоснов "
              "Мосгеотреста уточните систему координат у источника данных).")


if __name__ == "__main__":
    main()
