#!/usr/bin/env python3
"""
Парсер типовых DXF-файлов (генплан двора/участка) в JSON-формат
для веб-модуля генеративного озеленения.

Использование:
    python3 parse_dxf.py input.dxf --summary
    python3 parse_dxf.py input.dxf --out-dir output

Требуется: pip install ezdxf
"""

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import ezdxf

# ---------------------------------------------------------------------------
# КОНФИГУРАЦИЯ — правьте под слои своего конкретного DXF-файла.
# Сопоставление идёт по подстроке в имени слоя (без учёта регистра),
# проверяется в порядке списка, побеждает первое совпадение.
# ---------------------------------------------------------------------------

# Слои-полигоны -> зоны ограничений (RestrictionZone).
# ВАЖНО: порядок значим — более специфичные ключи (например OVERHEAD) должны
# стоять раньше более общих (POWER), иначе общее правило перехватит совпадение
# первым и специфичное никогда не сработает.
POLYGON_RULES = [
    ("BUILDING",    dict(type="building",            severity="forbidden", minDistance=3.0, message="Отступ от здания")),
    ("TRANSFORMER", dict(type="transformer",          severity="forbidden", minDistance=2.0, message="Трансформаторная подстанция")),
    ("ROAD",        dict(type="road",                 severity="forbidden", minDistance=1.0, message="Дорожное полотно — посадка запрещена")),
    ("PARK",        dict(type="custom",               severity="warning",   minDistance=1.0, message="Зона парковки")),
    ("PLAYGROUND",  dict(type="playground_zone",      severity="warning",   minDistance=1.0, message="Детская площадка")),
    ("GAS",         dict(type="gas_pipeline",         severity="forbidden", minDistance=2.0, message="Охранная зона газопровода")),
    ("SEWER",       dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона канализации")),
    ("WATER",       dict(type="water_pipeline",       severity="forbidden", minDistance=3.0, message="Охранная зона водопровода")),
    ("ELECTR",      dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электросети")),
    # Наземные ЛЭП (провода подвешены на опорах, ~9м над землёй) -- это НЕ то же
    # самое ограничение, что подземный кабель: под ними можно копать/сажать,
    # проблема только в предельной высоте кроны у ствола под проводом. Поэтому
    # отдельный тип с maxHeight и severity=warning, а не forbidden как у кабеля.
    ("OVERHEAD",    dict(type="overhead_power_line",  severity="warning",   minDistance=2.0, maxHeight=4.0,
                          message="Наземная ЛЭП — ограничение по высоте посадки под проводом (~9м), не запрет на посадку как таковую")),
    ("POWER",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    ("CABLE",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    ("HEAT",        dict(type="custom",               severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("WALKWAY",     dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PATH",        dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("SIDEWALK",    dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PROTECT",     dict(type="protected_zone",       severity="forbidden", minDistance=1.0, message="Охраняемая зона")),
    ("GRASS",       dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
    ("LAWN",        dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
]

# Слои, задающие границу участка (не ограничение, а boundary для генератора посадок)
BOUNDARY_LAYER_KEYWORDS = ["BOUNDARY", "TERRITORY", "SITE"]

# Слои, которые заведомо декоративны и не несут структурных данных — пропускаем
SKIP_LAYER_KEYWORDS = ["MARKING", "LABEL", "DIM", "TEXT"]

# Точечные объекты (деревья, кусты, лавочки, фонари...) -> SceneObject
# Ключ — подстрока в имени слоя.
POINT_LAYER_RULES = [
    ("TREE",       dict(type="tree",       model="/models/tree.glb")),
    ("BUSH",       dict(type="bush",       model="/models/bush.glb")),
    ("SHRUB",      dict(type="bush",       model="/models/bush.glb")),
    ("BENCH",      dict(type="bench",      model="/models/bench.glb")),
    ("LAMP",       dict(type="lamp",       model="/models/lamp.glb")),
    ("PLAYGROUND", dict(type="playground", model="/models/playground.glb")),
]

# Слой с 3D-мешами зданий (используется только для высоты)
BUILDING_MESH_LAYER_KEYWORDS = ["BUILDING"]

# Единицы DXF ($INSUNITS) -> метры
INSUNITS_TO_METERS = {0: 1.0, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 0.9144}


# ---------------------------------------------------------------------------

def layer_matches(layer_name: str, keywords) -> bool:
    up = layer_name.upper()
    return any(k in up for k in keywords)


def match_rule(layer_name: str, rules):
    up = layer_name.upper()
    for key, cfg in rules:
        if key in up:
            return cfg
    return None


def polygon_points(entity):
    """Вернуть список (x, y, z) для LWPOLYLINE/POLYLINE, без дублей подряд."""
    pts = []
    if entity.dxftype() == "LWPOLYLINE":
        elev = entity.dxf.elevation
        for p in entity.get_points():
            pts.append((float(p[0]), float(p[1]), float(elev)))
    elif entity.dxftype() == "POLYLINE":
        for v in entity.vertices:
            loc = v.dxf.location
            pts.append((float(loc.x), float(loc.y), float(loc.z)))
    # убрать подряд идущие почти-дубли (артефакты экспорта) и замыкающую точку
    cleaned = []
    for p in pts:
        if cleaned and math.hypot(p[0] - cleaned[-1][0], p[1] - cleaned[-1][1]) < 1e-6:
            continue
        cleaned.append(p)
    if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1e-6:
        cleaned.pop()
    return cleaned


def centroid(points):
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def buffer_segment(x1, y1, x2, y2, half_width):
    """Прямая (труба/кабель, заданная центральной линией) -> прямоугольная зона-коридор
    шириной 2*half_width вдоль неё (в плане XY, высота/глубина не учитывается)."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    nx, ny = -dy / length * half_width, dx / length * half_width
    return [(x1 + nx, y1 + ny, 0.0), (x2 + nx, y2 + ny, 0.0),
            (x2 - nx, y2 - ny, 0.0), (x1 - nx, y1 - ny, 0.0)]


class Transform:
    """DXF (x, y, z) -> Three.js (x, y=высота, z), с опциональным сдвигом origin."""

    def __init__(self, scale=1.0, origin_x=0.0, origin_y=0.0):
        self.scale = scale
        self.ox = origin_x
        self.oy = origin_y

    def point(self, x, y, z=0.0):
        return {
            "x": round((x - self.ox) * self.scale, 3),
            "y": round(z * self.scale, 3),
            "z": round((y - self.oy) * self.scale, 3),
        }

    def polygon(self, pts):
        return [{"x": round((x - self.ox) * self.scale, 3),
                  "z": round((y - self.oy) * self.scale, 3)} for x, y, *_ in pts]


# ---------------------------------------------------------------------------

def print_summary(doc):
    msp = doc.modelspace()
    print(f"DXF version: {doc.dxfversion}")
    insunits = doc.header.get("$INSUNITS", 0)
    print(f"$INSUNITS: {insunits} (~{INSUNITS_TO_METERS.get(insunits, 1.0)} м/ед.)")

    per_layer = {}
    for e in msp:
        per_layer.setdefault(e.dxf.layer, Counter())[e.dxftype()] += 1

    print("\nСлои и содержимое:")
    for layer, cnt in per_layer.items():
        print(f"  {layer:28s} {dict(cnt)}")

    xs, ys = [], []
    for e in msp:
        if e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            for x, y, *_ in polygon_points(e):
                xs.append(x); ys.append(y)
    if xs:
        print(f"\nBBox X: {min(xs):.2f} .. {max(xs):.2f}")
        print(f"BBox Y: {min(ys):.2f} .. {max(ys):.2f}")


def extract_boundary(msp, tf):
    for e in msp.query("LWPOLYLINE POLYLINE"):
        if layer_matches(e.dxf.layer, BOUNDARY_LAYER_KEYWORDS):
            pts = polygon_points(e)
            if len(pts) >= 3:
                return {"polygon": tf.polygon(pts), "sourceLayer": e.dxf.layer}
    return None


def _add_zone(zones, idx_by_type, cfg, layer, pts_xyz, tf):
    idx_by_type[cfg["type"]] += 1
    zone = {
        "id": f"{cfg['type']}_{idx_by_type[cfg['type']]:03d}",
        "type": cfg["type"],
        "name": layer,
        "polygon": tf.polygon(pts_xyz),
        "severity": cfg["severity"],
        "minDistance": cfg["minDistance"],
        "message": cfg["message"],
    }
    # extra, type-specific fields (e.g. maxHeight for overhead power lines) ride
    # along unchanged -- anything in POLYGON_RULES beyond the core keys above
    for k, v in cfg.items():
        if k not in zone:
            zone[k] = v
    zones.append(zone)


def extract_restrictions(msp, tf):
    """Закрытые LWPOLYLINE/POLYLINE -> зона-полигон как есть.
    LINE и открытые (не замкнутые) POLYLINE -> трактуются как трасса трубы/кабеля
    и раздуваются в прямоугольный коридор шириной 2*minDistance. Строго вертикальные
    участки (стояки-подключения к зданию, где меняется только Z) пропускаются —
    это не горизонтальное ограничение в плане XZ."""
    zones = []
    idx_by_type = Counter()

    for e in msp.query("LWPOLYLINE POLYLINE"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        is_closed = e.closed if e.dxftype() == "LWPOLYLINE" else e.is_closed
        pts = polygon_points(e)
        if is_closed:
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf)
        else:
            for (x1, y1, _z1), (x2, y2, _z2) in zip(pts, pts[1:]):
                if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                    continue  # чисто вертикальный стояк — не ограничение в плане
                seg = buffer_segment(x1, y1, x2, y2, cfg["minDistance"])
                if seg:
                    _add_zone(zones, idx_by_type, cfg, layer, seg, tf)

    for e in msp.query("LINE"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        s, en = e.dxf.start, e.dxf.end
        if math.hypot(en.x - s.x, en.y - s.y) < 1e-6:
            continue
        seg = buffer_segment(s.x, s.y, en.x, en.y, cfg["minDistance"])
        if seg:
            _add_zone(zones, idx_by_type, cfg, layer, seg, tf)

    return zones


def nearest_text(x, y, texts):
    best, best_d = None, None
    for t in texts:
        d = math.hypot(t["x"] - x, t["y"] - y)
        if best_d is None or d < best_d:
            best, best_d = t, d
    return best


_LABEL_HEIGHT_SUFFIX = re.compile(r"\s+h=[\d.]+\s*m\s*$", re.IGNORECASE)

def clean_label(text):
    """Strip a trailing " h=27.0m"-style height annotation some generators bake
    into the label text itself, so the building name comes through clean."""
    return _LABEL_HEIGHT_SUFFIX.sub("", text).strip()


def extract_buildings(msp, tf, restriction_zones):
    """Здания как SceneObject (для 3D-модели), высота — из MESH, имя — из ближайшего TEXT."""
    texts = []
    for e in msp.query("TEXT MTEXT"):
        try:
            ins = e.dxf.insert
            texts.append({"x": float(ins.x), "y": float(ins.y), "text": e.dxf.text})
        except Exception:
            continue

    meshes = []
    for e in msp.query("MESH"):
        if not layer_matches(e.dxf.layer, BUILDING_MESH_LAYER_KEYWORDS):
            continue
        verts = list(e.vertices)
        if not verts:
            continue
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        height = max(v[2] for v in verts)
        meshes.append({"x": cx, "y": cy, "height": height})

    objects = []
    building_zones = [z for z in restriction_zones if z["type"] == "building"]
    for i, zone in enumerate(building_zones, start=1):
        xs = [p["x"] for p in zone["polygon"]]
        zs = [p["z"] for p in zone["polygon"]]
        cx_local, cz_local = sum(xs) / len(xs), sum(zs) / len(zs)
        # обратная трансформация центра в исходные DXF-координаты для поиска ближайшего меша/текста
        cx_dxf = cx_local / tf.scale + tf.ox
        cy_dxf = cz_local / tf.scale + tf.oy

        height = None
        if meshes:
            m = min(meshes, key=lambda m: math.hypot(m["x"] - cx_dxf, m["y"] - cy_dxf))
            height = round(m["height"] * tf.scale, 2)

        label = nearest_text(cx_dxf, cy_dxf, texts) if texts else None

        objects.append({
            "id": f"building_{i:03d}",
            "type": "building",
            "model": "/models/house.glb",
            "position": {"x": round(cx_local, 3), "y": 0, "z": round(cz_local, 3)},
            "rotation": 0,
            "scale": 1,
            "metadata": {
                "name": clean_label(label["text"]) if label else zone["name"],
                "height": height,
                "footprint": zone["polygon"],
                "sourceLayer": zone["name"],
            },
        })
    return objects


def extract_point_objects(msp, tf):
    """INSERT/POINT — по одному объекту на сущность. LINE+CIRCLE в одном слое (столб+плафон,
    как в типовых DXF для фонарей) группируются по совпадающей (x, y) в один объект."""
    objects = []
    counters = Counter()

    for layer_keyword, cfg in POINT_LAYER_RULES:
        entities = [e for e in msp if layer_matches(e.dxf.layer, [layer_keyword])]
        if not entities:
            continue

        inserts_or_points = [e for e in entities if e.dxftype() in ("INSERT", "POINT")]
        lines = [e for e in entities if e.dxftype() == "LINE"]
        circles = [e for e in entities if e.dxftype() == "CIRCLE"]

        for e in inserts_or_points:
            loc = e.dxf.insert if e.dxftype() == "INSERT" else e.dxf.location
            counters[cfg["type"]] += 1
            objects.append({
                "id": f"{cfg['type']}_{counters[cfg['type']]:03d}",
                "type": cfg["type"],
                "model": cfg["model"],
                "position": tf.point(loc.x, loc.y, loc.z if e.dxftype() == "INSERT" else 0.0),
                "rotation": math.radians(getattr(e.dxf, "rotation", 0.0)),
                "scale": getattr(e.dxf, "xscale", 1.0),
                "metadata": {"blockName": getattr(e.dxf, "name", None), "sourceLayer": e.dxf.layer},
            })

        # LINE (столб от земли вверх) + CIRCLE (плафон на верхушке) в одном месте -> один объект
        if lines and circles and not inserts_or_points:
            seen = set()
            for ln in lines:
                x, y = round(ln.dxf.start.x, 3), round(ln.dxf.start.y, 3)
                key = (x, y)
                if key in seen:
                    continue
                seen.add(key)
                top_z = max(ln.dxf.start.z, ln.dxf.end.z)
                # уточнить высоту по ближайшему CIRCLE, если он есть
                near = min(circles, key=lambda c: math.hypot(c.dxf.center.x - x, c.dxf.center.y - y), default=None)
                if near is not None:
                    top_z = max(top_z, near.dxf.center.z)
                counters[cfg["type"]] += 1
                objects.append({
                    "id": f"{cfg['type']}_{counters[cfg['type']]:03d}",
                    "type": cfg["type"],
                    "model": cfg["model"],
                    "position": tf.point(x, y, 0.0),
                    "rotation": 0,
                    "scale": 1,
                    "metadata": {"height": round(top_z * tf.scale, 2), "sourceLayer": ln.dxf.layer},
                })

    return objects


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Путь к .dxf файлу")
    ap.add_argument("--out-dir", default="output", help="Куда писать JSON (по умолчанию ./output)")
    ap.add_argument("--summary", action="store_true", help="Только показать слои/сущности, ничего не писать")
    ap.add_argument("--scale", type=float, default=None, help="Множитель до метров (по умолчанию — авто по $INSUNITS)")
    ap.add_argument("--no-center", action="store_true", help="Не центрировать координаты по границе участка")
    args = ap.parse_args()

    try:
        doc = ezdxf.readfile(args.input)
    except IOError:
        sys.exit(f"Не удалось открыть файл: {args.input}")
    except ezdxf.DXFStructureError:
        sys.exit(f"Файл повреждён или не является корректным DXF: {args.input}")

    if args.summary:
        print_summary(doc)
        return

    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS", 0)
    scale = args.scale if args.scale is not None else INSUNITS_TO_METERS.get(insunits, 1.0)

    origin_x, origin_y = 0.0, 0.0
    if not args.no_center:
        for e in msp.query("LWPOLYLINE POLYLINE"):
            if layer_matches(e.dxf.layer, BOUNDARY_LAYER_KEYWORDS):
                pts = polygon_points(e)
                if pts:
                    origin_x, origin_y = centroid(pts)
                break

    tf = Transform(scale=scale, origin_x=origin_x, origin_y=origin_y)

    boundary = extract_boundary(msp, tf)
    restrictions = extract_restrictions(msp, tf)
    buildings = extract_buildings(msp, tf, restrictions)
    points = extract_point_objects(msp, tf)
    objects = buildings + points

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "boundary.json").write_text(
        json.dumps(boundary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "restrictions.json").write_text(
        json.dumps(restrictions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "objects.json").write_text(
        json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Масштаб: {scale} м/ед. (INSUNITS={insunits}), origin=({origin_x:.2f}, {origin_y:.2f})")
    print(f"boundary.json      — {'1 полигон' if boundary else 'не найден'}")
    print(f"restrictions.json  — {len(restrictions)} зон")
    print(f"objects.json       — {len(objects)} объектов ({len(buildings)} зданий, {len(points)} точечных)")
    print(f"\nЗаписано в {out_dir.resolve()}")


if __name__ == "__main__":
    main()
