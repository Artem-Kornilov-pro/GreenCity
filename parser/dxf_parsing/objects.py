"""
Объекты сцены: здания (контур + высота из 3D-мешей), точечные объекты по
слоям (деревья, кусты, МАФ, фонари -- INSERT/POINT/CIRCLE), фасадные элементы
(окна, козырьки) и бордюры.
"""

import math
import re
from collections import Counter

from shapely.geometry import LineString
from shapely.strtree import STRtree

from dxf_parsing.geometry import polygon_points
from dxf_parsing.layer_names import layer_key_of
from dxf_parsing.rules import (
    BUILDING_MESH_LAYER_KEYWORDS,
    CURB_LAYER_KEYWORDS,
    FACADE_LAYER_KEYWORDS,
    POINT_LAYER_EXCLUDE_KEYWORDS,
    POINT_LAYER_RULES,
    _clamp_render_scale,
    layer_matches,
)


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


# Отметка дальше этого от нуля -- абсолютная высота над уровнем моря
# (Москва -- 120-250 м), а не высота над землёй участка.
_ABSOLUTE_ELEVATION_M = 5.0


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
        if not layer_matches(layer_key_of(msp, e), BUILDING_MESH_LAYER_KEYWORDS):
            continue
        verts = list(e.vertices)
        if not verts:
            continue
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        top, base = max(v[2] for v in verts), min(v[2] for v in verts)
        # Топосъёмка в абсолютных отметках: дом стоит на высоте 150 м, и его
        # высота -- разница отметок, а не отметка крыши.
        height = top - base if abs(base * tf.scale) > _ABSOLUTE_ELEVATION_M else top
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


def _metadata(cfg, **fields):
    """metadata точечного объекта; у слоя-вида (правило из справочника видов,
    rules._species_point_rules) -- ещё и название вида."""
    if cfg.get("species"):
        fields["species"] = cfg["species"]
    return fields


# Точечные объекты ближе этого друг к другу -- одна и та же посадка/опора,
# попавшая в сцену несколько раз.
_DUPLICATE_POINT_TOLERANCE_M = 0.1
# Дерево и куст в одной точке -- одно растение: слой-привязка вроде
# "(ГП) Привязка дер и куст" ставит метку прямо на дерево.
_PLANT_TYPES = ("tree", "bush")
# Окружности одного условного знака -- центры ближе этого. В топосъёмке
# Мосгеотреста знак дерева бывает продублирован (та же дуга дважды или
# со сдвигом в сантиметры), а знаки соседних деревьев почти всегда дальше
# 0,5 м друг от друга. Порог взят с запасом в меньшую сторону: лишнее
# существующее дерево безопаснее потерянного -- рядом с ним не посадят новое.
_SYMBOL_MERGE_M = 0.15
# Дуга с таким недобором до полного оборота -- всё ещё окружность.
_FULL_TURN_TOLERANCE_DEG = 0.1


def dedupe_point_objects(objects):
    """Один объект на место: пачка DWG сливается по слоям, и одна посадка
    приходит из нескольких файлов. Из дублей остаётся первый, но дерево
    важнее куста, а объект с названием вида -- безымянного."""
    kept = {}
    order = []
    for obj in objects:
        pos = obj["position"]
        family = "plant" if obj["type"] in _PLANT_TYPES else obj["type"]
        key = (family, round(pos["x"] / _DUPLICATE_POINT_TOLERANCE_M), round(pos["z"] / _DUPLICATE_POINT_TOLERANCE_M))
        current = kept.get(key)
        if current is None:
            kept[key] = obj
            order.append(key)
            continue
        better_type = obj["type"] == "tree" and current["type"] == "bush"
        better_name = obj["type"] == current["type"] and obj["metadata"].get("species") and not current["metadata"].get("species")
        if better_type or better_name:
            kept[key] = obj
    return [kept[key] for key in order]


def extract_point_objects(msp, tf):
    """INSERT/POINT — по одному объекту на сущность. LINE+CIRCLE в одном слое (столб+плафон,
    как в типовых DXF для фонарей) группируются по совпадающей (x, y) в один объект."""
    objects = []
    counters = Counter()

    # Правил сотни (по одному на вид), поэтому ключевые слова сопоставляются
    # с ключами слоёв один раз, а сущности берутся из индекса по ключу. Ключ --
    # имя слоя, у вида чертежа -- с учётом синонимов и имени блока
    # (layer_names.layer_key_of).
    entities_by_layer: dict[str, list] = {}
    for e in msp:
        entities_by_layer.setdefault(layer_key_of(msp, e), []).append(e)

    for layer_keyword, cfg in POINT_LAYER_RULES:
        entities = [
            e
            for layer_name, layer_entities in entities_by_layer.items()
            if layer_matches(layer_name, [layer_keyword]) and not layer_matches(layer_name, POINT_LAYER_EXCLUDE_KEYWORDS)
            for e in layer_entities
        ]
        if not entities:
            continue

        inserts_or_points = [e for e in entities if e.dxftype() in ("INSERT", "POINT")]
        # Столб -- вертикальный отрезок. Отрезки в плане -- штрихи условного
        # знака (крест в круге дерева) или кабель на слое освещения: столбами
        # они не считаются, иначе каждый штрих стал бы объектом.
        lines = [e for e in entities if e.dxftype() == "LINE" and _is_vertical(e)]
        # Дуга на полный оборот -- окружность только у растений: у фонаря
        # такая дуга -- плафон в метре от столба, а не второй фонарь.
        full_arcs = cfg["type"] in _PLANT_TYPES
        circles = _symbol_circles([e for e in entities if _is_circle(e, full_arcs)], _SYMBOL_MERGE_M / tf.scale)

        for e in inserts_or_points:
            loc = e.dxf.insert if e.dxftype() == "INSERT" else e.dxf.location
            counters[cfg["type"]] += 1
            objects.append({
                "id": f"{cfg['type']}_{counters[cfg['type']]:03d}",
                "type": cfg["type"],
                "model": cfg["model"],
                "position": tf.point(loc.x, loc.y, loc.z if e.dxftype() == "INSERT" else 0.0),
                "rotation": math.radians(getattr(e.dxf, "rotation", 0.0)),
                "scale": _clamp_render_scale(getattr(e.dxf, "xscale", 1.0)),
                "metadata": _metadata(cfg, blockName=getattr(e.dxf, "name", None), sourceLayer=e.dxf.layer),
            })

        # LINE (столб) и CIRCLE (плафон) в одном месте -> один объект. На
        # слое могут быть и такие объекты, и INSERT/POINT одновременно.
        if lines and circles:
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
                    "metadata": _metadata(cfg, height=round(top_z * tf.scale, 2), sourceLayer=ln.dxf.layer),
                })

        # Одиночный CIRCLE без пары LINE -- маркер точки (например дверь
        # подъезда). CIRCLE ровно в точке INSERT/POINT того же слоя -- маркер
        # уже учтённой точки (так пишет наш экспорт), а не второй объект.
        elif circles and not lines:
            point_keys = {
                (round(loc.x, 3), round(loc.y, 3))
                for loc in (e.dxf.insert if e.dxftype() == "INSERT" else e.dxf.location for e in inserts_or_points)
            }
            for c in circles:
                if (round(c.dxf.center.x, 3), round(c.dxf.center.y, 3)) in point_keys:
                    continue
                counters[cfg["type"]] += 1
                objects.append({
                    "id": f"{cfg['type']}_{counters[cfg['type']]:03d}",
                    "type": cfg["type"],
                    "model": cfg["model"],
                    "position": tf.point(c.dxf.center.x, c.dxf.center.y, c.dxf.center.z),
                    "rotation": 0,
                    "scale": 1,
                    "metadata": _metadata(cfg, sourceLayer=c.dxf.layer),
                })

    return objects


def _is_circle(e, full_arcs: bool) -> bool:
    """CIRCLE или (при full_arcs) дуга на полный оборот: в топосъёмке
    Мосгеотреста знак отдельно стоящего дерева нарисован именно такой дугой
    (ARC 0..360°), без блока."""
    if e.dxftype() == "CIRCLE":
        return True
    if not full_arcs or e.dxftype() != "ARC":
        return False
    span = (e.dxf.end_angle - e.dxf.start_angle) % 360
    return span < _FULL_TURN_TOLERANCE_DEG or span > 360 - _FULL_TURN_TOLERANCE_DEG


def _symbol_circles(circles, merge_distance):
    """По одной окружности на условный знак: в знаке бывает несколько
    концентрических или почти совпадающих окружностей (обвод, крона), и
    каждая не должна стать отдельным объектом. Остаётся первая окружность
    из каждой группы с центрами ближе merge_distance друг к другу."""
    if len(circles) < 2:
        return circles
    kept, cells = [], {}
    for c in circles:
        x, y = c.dxf.center.x, c.dxf.center.y
        gx, gy = math.floor(x / merge_distance), math.floor(y / merge_distance)
        near = any(
            math.hypot(x - ox, y - oy) < merge_distance
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for ox, oy in cells.get((gx + dx, gy + dy), ())
        )
        if near:
            continue
        cells.setdefault((gx, gy), []).append((x, y))
        kept.append(c)
    return kept


def _is_vertical(line) -> bool:
    s, en = line.dxf.start, line.dxf.end
    return math.hypot(en.x - s.x, en.y - s.y) < 1e-6 and abs(en.z - s.z) > 1e-6


def extract_facade_quads(msp, tf):
    """3DFACE-элементы окон/козырьков -- не самостоятельные объекты сцены (их
    сотни-тысячи и они не переставляются), а геометрия для отрисовки прямо на
    фасаде здания. Каждая грань -- 4 вершины в 3D (уже с учётом высоты)."""
    result = {name: [] for name in FACADE_LAYER_KEYWORDS}
    for e in msp.query("3DFACE"):
        key = layer_key_of(msp, e)
        for name, keywords in FACADE_LAYER_KEYWORDS.items():
            if layer_matches(key, keywords):
                quad = [e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2, e.dxf.vtx3]
                result[name].append([tf.point(v.x, v.y, v.z) for v in quad])
                break
    return result


def extract_curb_polylines(msp, tf):
    """LWPOLYLINE/POLYLINE/LINE на слоях-бордюрах -- не зона ограничения и не
    самостоятельный объект, а линии для схематичной ribbon-отрисовки прямо на
    земле (см. frontend CurbStrips.tsx). Каждая полилиния -- список 2D-точек
    (в отличие от фасадных квадов: бордюр лежит на земле, высота не нужна)."""
    result = []
    for e in msp.query("LWPOLYLINE POLYLINE"):
        if not layer_matches(layer_key_of(msp, e), CURB_LAYER_KEYWORDS):
            continue
        pts = polygon_points(e)
        if len(pts) >= 2:
            result.append(tf.polygon(pts))
    for e in msp.query("LINE"):
        if not layer_matches(layer_key_of(msp, e), CURB_LAYER_KEYWORDS):
            continue
        s, en = e.dxf.start, e.dxf.end
        if math.hypot(en.x - s.x, en.y - s.y) < 1e-6:
            continue
        result.append(tf.polygon([(s.x, s.y, 0.0), (en.x, en.y, 0.0)]))
    return dedupe_curbs(result)


# Отрезок бордюра, который почти целиком (_CURB_COVERED_SHARE длины) лежит в
# полосе _CURB_DUPLICATE_TOLERANCE_M от уже взятого, -- тот же борт второй раз.
_CURB_DUPLICATE_TOLERANCE_M = 0.05
_CURB_COVERED_SHARE = 0.9


def dedupe_curbs(polylines):
    """Один бордюр на место: один и тот же борт приходит и из топосъёмки, и
    из проекта, и совпадающие полоски мерцали бы в 3D. Отрезки-дубли
    выбрасываются (первый остаётся), остальные куски склеиваются обратно."""
    segments, owners = [], []
    for index, pl in enumerate(polylines):
        for a, b in zip(pl, pl[1:]):
            if (a["x"], a["z"]) != (b["x"], b["z"]):
                segments.append(LineString([(a["x"], a["z"]), (b["x"], b["z"])]))
                owners.append(index)
    if not segments:
        return polylines
    tree = STRtree(segments)
    duplicate = [False] * len(segments)
    for i, seg in enumerate(segments):
        for j in tree.query(seg.buffer(_CURB_DUPLICATE_TOLERANCE_M)):
            if j >= i or duplicate[j]:
                continue
            band = segments[j].buffer(_CURB_DUPLICATE_TOLERANCE_M, cap_style=2)
            if seg.intersection(band).length >= _CURB_COVERED_SHARE * seg.length:
                duplicate[i] = True
                break
    if not any(duplicate):
        return polylines

    result, run, run_owner = [], [], None

    def flush():
        if len(run) >= 2:
            result.append([{"x": x, "z": z} for x, z in run])

    for seg, owner, dup in zip(segments, owners, duplicate):
        (x0, z0), (x1, z1) = seg.coords
        if dup or owner != run_owner or not run or run[-1] != (x0, z0):
            flush()
            run, run_owner = ([] if dup else [(x0, z0)]), owner
        if not dup:
            run.append((x1, z1))
    flush()
    return result


# ---------------------------------------------------------------------------
