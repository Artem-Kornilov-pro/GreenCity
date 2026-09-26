"""
Объекты сцены: здания (контур + высота из 3D-мешей), точечные объекты по
слоям (деревья, кусты, МАФ, фонари -- INSERT/POINT/CIRCLE), фасадные элементы
(окна, козырьки) и бордюры.
"""

import math
import re
from collections import Counter

from dxf_parsing.geometry import polygon_points
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


def _metadata(cfg, **fields):
    """metadata точечного объекта; у слоя-вида (правило из справочника видов,
    rules._species_point_rules) -- ещё и название вида."""
    if cfg.get("species"):
        fields["species"] = cfg["species"]
    return fields


def extract_point_objects(msp, tf):
    """INSERT/POINT — по одному объекту на сущность. LINE+CIRCLE в одном слое (столб+плафон,
    как в типовых DXF для фонарей) группируются по совпадающей (x, y) в один объект."""
    objects = []
    counters = Counter()

    # Индекс по имени слоя строится один раз: POINT_LAYER_RULES теперь
    # включает по записи на каждый вид из справочника (~250 правил вместо
    # ~17, issue #50 follow-up) -- наивный проход по ВСЕМ сущностям
    # modelspace на КАЖДОЕ правило на крупных сценах (десятки тысяч сущностей)
    # был бы уже заметно медленнее. Различных имён слоёв на порядки меньше,
    # чем сущностей, поэтому матчинг по ключевым словам делаем по ним, а
    # сущности достаём из готового индекса.
    entities_by_layer: dict[str, list] = {}
    for e in msp:
        entities_by_layer.setdefault(e.dxf.layer, []).append(e)

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
                "scale": _clamp_render_scale(getattr(e.dxf, "xscale", 1.0)),
                "metadata": _metadata(cfg, blockName=getattr(e.dxf, "name", None), sourceLayer=e.dxf.layer),
            })

        # LINE (столб от земли вверх) + CIRCLE (плафон на верхушке) в одном месте -> один объект.
        # НЕ требуем "and not inserts_or_points": это разные сущности одного
        # DXF-типа (LINE/CIRCLE против INSERT/POINT), а не альтернативные
        # прочтения одних и тех же данных -- на одном слое может быть и то,
        # и другое одновременно (19_2ya_pryadilnaya: 443 "голых" CIRCLE-дерева
        # из исходной топосъёмки + INSERT-деревья, добавленные поверх). Раньше
        # с "and not inserts_or_points" появление хотя бы одного INSERT на
        # слое молча выбрасывало ВСЕ CIRCLE-объекты того же слоя.
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

        # Одиночные CIRCLE без пары LINE -- просто маркер точки (напр. дверь
        # подъезда на слое ENTRANCES), а не фонарный столб. Та же правка, что
        # и выше: не требуем "and not inserts_or_points".
        #
        # Но CIRCLE ровно в точке INSERT/POINT того же слоя -- это видимый
        # маркер уже учтённой точки, а не второй объект: так пишет каждую
        # посадку наш же экспорт (exchange/export_dxf.py -- POINT + CIRCLE,
        # потому что голую точку CAD-просмотрщики не показывают). Без этого
        # повторная загрузка экспортированного плана удваивала все посадки.
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


def extract_facade_quads(msp, tf):
    """3DFACE-элементы окон/козырьков -- не самостоятельные объекты сцены (их
    сотни-тысячи и они не переставляются), а геометрия для отрисовки прямо на
    фасаде здания. Каждая грань -- 4 вершины в 3D (уже с учётом высоты)."""
    result = {name: [] for name in FACADE_LAYER_KEYWORDS}
    for e in msp.query("3DFACE"):
        for name, keywords in FACADE_LAYER_KEYWORDS.items():
            if layer_matches(e.dxf.layer, keywords):
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
        if not layer_matches(e.dxf.layer, CURB_LAYER_KEYWORDS):
            continue
        pts = polygon_points(e)
        if len(pts) >= 2:
            result.append(tf.polygon(pts))
    for e in msp.query("LINE"):
        if not layer_matches(e.dxf.layer, CURB_LAYER_KEYWORDS):
            continue
        s, en = e.dxf.start, e.dxf.end
        if math.hypot(en.x - s.x, en.y - s.y) < 1e-6:
            continue
        result.append(tf.polygon([(s.x, s.y, 0.0), (en.x, en.y, 0.0)]))
    return result


# ---------------------------------------------------------------------------
