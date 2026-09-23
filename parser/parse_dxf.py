#!/usr/bin/env python3
"""
Парсер типовых DXF-файлов (генплан двора/участка) в JSON-формат
для веб-модуля генеративного озеленения.

Использование:
    python3 parse_dxf.py input.dxf --summary
    python3 parse_dxf.py input.dxf --out-dir output

Требуется: pip install ezdxf shapely
"""

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import ezdxf
import shapely
from ezdxf import path as ezpath
from shapely import concave_hull
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import linemerge

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
    # minDistance -- базовое (кустарник); для дерева фронтенд применяет 5.0 м
    # из своего каталога норм (frontend/src/setbackNorms.ts) -- отступ по
    # СНиП 2.07.01-89*/СП 42.13330.2016 различается по виду посадки.
    ("BUILDING",    dict(type="building",            severity="forbidden", minDistance=1.5,
                          message="Отступ от здания: дерево — 5 м, кустарник — 1.5 м")),
    ("TRANSFORMER", dict(type="transformer",          severity="forbidden", minDistance=2.0, message="Трансформаторная подстанция")),
    ("ROAD",        dict(type="road",                 severity="forbidden", minDistance=1.0, message="Дорожное полотно — посадка запрещена")),
    ("PARK",        dict(type="custom",               severity="warning",   minDistance=1.0, message="Зона парковки")),
    ("PLAYGROUND",  dict(type="playground_zone",      severity="warning",   minDistance=1.0, message="Детская площадка")),
    ("GAS",         dict(type="gas_pipeline",         severity="forbidden", minDistance=2.0, message="Охранная зона газопровода")),
    ("SEWER",       dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона канализации")),
    ("WATER",       dict(type="water_pipeline",       severity="forbidden", minDistance=3.0, message="Охранная зона водопровода")),
    # Слаботочка (связь/телефон/оптика) -- НЕ то же самое ограничение, что
    # силовой кабель: тонкий кабель без высокого напряжения, повреждение
    # корнями/при раскопке не несёт той же опасности (нет риска поражения
    # током/пожара), поэтому нормы дают заметно меньший отступ, не как у
    # силовых линий. Проверяется ДО общего "ELECTR"/"CABLE" ниже — иначе те
    # перехватят совпадение первыми (слой называется "ELECTR_CABLE_COMM",
    # содержит подстроку "ELECTR"). severity=warning (не блокирует посадку
    # как таковую), а не forbidden, как у силовой сети. Найдено на проекте
    # 11 (Фрунзенская набережная, см. DWG_TO_DXF_INTEGRATION_GUIDE.md п.11.1):
    # тип "electrical" в одиночку перекрывал 83% площади участка из-за
    # смешения силового кабеля и кабеля связи в один и тот же forbidden-тип.
    ("CABLE_COMM",  dict(type="signal_cable",         severity="warning",   minDistance=0.5,
                          message="Кабель связи — слаботочная сеть, отступ меньше, чем у силового кабеля")),
    ("ELECTR",      dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электросети")),
    # Наземные ЛЭП (провода подвешены на опорах, ~9м над землёй) -- это НЕ то же
    # самое ограничение, что подземный кабель: под ними можно копать/сажать,
    # проблема только в предельной высоте кроны у ствола под проводом. Поэтому
    # отдельный тип с maxHeight и severity=warning, а не forbidden как у кабеля.
    ("OVERHEAD",    dict(type="overhead_power_line",  severity="warning",   minDistance=2.0, maxHeight=4.0,
                          message="Наземная ЛЭП — ограничение по высоте посадки под проводом (~9м), не запрет на посадку как таковую")),
    ("POWER",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    ("CABLE",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    # Свой тип, а не "custom": у теплосети своя строка в таблице отступов
    # (СП 42.13330, табл. 9.1; 743-ПП, табл. 3.6.1 -- дерево 2 м, кустарник
    # 1 м) и свои правила по породе (МГСН 1.02-02, п. 4.2.8) -- см.
    # backend/setback_norms.py. Под "custom" они не применялись вовсе.
    ("HEAT",        dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("WALKWAY",     dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PATH",        dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("SIDEWALK",    dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PROTECT",     dict(type="protected_zone",       severity="forbidden", minDistance=1.0, message="Охраняемая зона")),
    ("GRASS",       dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
    ("LAWN",        dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
    # Открытая земля -- НЕ из плана покрытий (того может не быть вовсе), а
    # вычислена при склейке DXF как граница участка минус здания/дорога/
    # тротуар (см. convert_dtset/convert_generic.py::add_ground_zone). Без
    # неё генератор либо сажал по ВСЕЙ площади границы, включая непокрытые
    # места без данных (если нет GRASS вообще), либо не сажал нигде за
    # пределами явно размеченного газона (если план покрытий есть, но
    # покрывает не весь участок) -- см. историю правок и DWG_TO_DXF_
    # INTEGRATION_GUIDE.md. severity=allowed, как у газона -- generate-
    # greenery уже трактует любую allowed-зону как площадку под посадку
    # (_planting_zone_shapes в backend/greenery_generator.py фильтрует по
    # severity, не по типу), отдельной правки бэкенда не потребовалось.
    ("GROUND",      dict(type="protected_zone",       severity="allowed",   minDistance=0.0,
                          message="Открытая земля — вычислено как участок минус здания/дорога/тротуар")),

    # Русские названия (issue #50 follow-up) -- та же логика специфичности,
    # что и у английского блока выше: более узкое правило (слаботочка) идёт
    # раньше более общего (силовой кабель/электросеть), иначе общее
    # перехватит совпадение первым. Подтверждено реальными слоями проектов
    # Мосгеотреста: landscaping-файл "13_kharkovskaya" ("Борт_БР100.30.15",
    # "Газон_Рулонный", "Устройство_трот...") И его собственный топоплан
    # (Xrefs/output*_tp.dwg -- "Здания", "Граница улицы", "Леса и газоны") и
    # подземка (Xrefs/output*_up.dwg -- "Водопровод", "Канализация
    # самотёчная", "Водосток", "Дренаж", "Общий коллектор", "Газопровод",
    # "Теплосеть", "Кабель электрический"/"Кабели"/"Кабель защиты", "Кабель
    # связи") -- отдельные DWG-файлы того же проекта, не включённые в
    # изначально протестированный набор "Проектное решение" (только сама
    # посадка), см. переписку issue #50.
    ("ЗДАНИ",       dict(type="building",             severity="forbidden", minDistance=1.5,
                          message="Отступ от здания: дерево — 5 м, кустарник — 1.5 м")),
    ("ГАЗОПРОВОД",  dict(type="gas_pipeline",         severity="forbidden", minDistance=2.0, message="Охранная зона газопровода")),
    ("КАНАЛИЗАЦ",   dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона канализации")),
    ("ВОДОСТОК",    dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона водостока")),
    ("ДРЕНАЖ",      dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона дренажа")),
    ("КОЛЛЕКТОР",   dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона общего коллектора коммуникаций")),
    ("ВОДОПРОВОД",  dict(type="water_pipeline",       severity="forbidden", minDistance=3.0, message="Охранная зона водопровода")),
    ("СВЯЗ",        dict(type="signal_cable",         severity="warning",   minDistance=0.5,
                          message="Кабель связи — слаботочная сеть, отступ меньше, чем у силового кабеля")),
    ("ТЕПЛОСЕТ",    dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    # Так теплосеть называют сами нормативы (743-ПП -- "теплопровод", МГСН
    # 1.02-02 -- "теплотрасса"); слой проектировщика может быть назван так же.
    ("ТЕПЛОТРАСС",  dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("ТЕПЛОПРОВОД", dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("ЛЭП",         dict(type="overhead_power_line",  severity="warning",   minDistance=2.0, maxHeight=4.0,
                          message="Наземная ЛЭП — ограничение по высоте посадки под проводом (~9м), не запрет на посадку как таковую")),
    ("ЭЛЕКТР",      dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электросети")),
    ("КАБЕЛ",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    # Общий "подземные коммуникации" без уточнения вида -- самый широкий из
    # русского блока, поэтому идёт последним среди коммуникаций: если бы стоял
    # раньше -- перехватывал бы совпадение у более специфичных правил выше
    # (газопровод, канализация и т.д.), которые тоже "подземные коммуникации"
    # по сути, но с известным точным нормативом.
    ("ПОДЗЕМН",     dict(type="custom",               severity="forbidden", minDistance=2.0, message="Неуточнённые подземные коммуникации")),
    ("ТРОТ",        dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка/тротуар")),
    ("УЛИЦ",        dict(type="road",                 severity="forbidden", minDistance=1.0, message="Проезжая часть улицы — посадка запрещена")),
    ("ГАЗОН",       dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
]

# Слои, задающие границу участка (не ограничение, а boundary для генератора посадок)
BOUNDARY_LAYER_KEYWORDS = ["BOUNDARY", "TERRITORY", "SITE"]

# Слои, которые заведомо декоративны и не несут структурных данных — пропускаем
SKIP_LAYER_KEYWORDS = ["MARKING", "LABEL", "DIM", "TEXT"]

# Точечные объекты (деревья, кусты, лавочки, фонари...) -> SceneObject
# Ключ — подстрока в имени слоя.
# Слои, которые совпадают с одним из ключей POINT_LAYER_RULES по подстроке,
# но на самом деле не дискретные точечные объекты, а линейная/площадная зона
# (issue #50 follow-up, реальный случай: "Полоса деревьев" -- топоплан
# Мосгеотреста, полоса/пятно существующей растительности вдоль улицы,
# отрисованная топосъёмкой как ~15600 разрозненных LINE-фрагментов контура,
# а НЕ по объекту на дерево). Слово "ДЕРЕВ" внутри совпадает с общим
# keyword'ом ниже -- без этого исключения LINE+CIRCLE-логика "столб+плафон"
# (задумана для фонарей) плодит по объекту на каждый уникальный конец
# фрагмента, то есть тысячи фантомных деревьев на одном линтайпе. Зона как
# таковая (полигон полосы) пока не реконструируется -- это отдельная, более
# сложная задача (см. _reconstruct_buildings_from_line_fragments для здания
# как прецедент), сейчас слой просто не попадает ни в объекты, ни в зоны.
POINT_LAYER_EXCLUDE_KEYWORDS = ["ПОЛОСА"]

POINT_LAYER_RULES = [
    ("TREE",       dict(type="tree",       model="/models/tree.glb")),
    ("BUSH",       dict(type="bush",       model="/models/bush.glb")),
    ("SHRUB",      dict(type="bush",       model="/models/bush.glb")),
    ("BENCH",      dict(type="bench",      model="/models/bench.glb")),
    # Шезлонг -- отдельный от лавки тип (см. frontend/public/models/README.md:
    # "шезлонг, стол и табурет не должны все считаться bench"), у него уже
    # есть настоящая .glb-модель из пака МАФ (lounger_narrow/lounger_wide,
    # backend/catalog_generated.json), в отличие от большинства типов ниже.
    ("LOUNGER",    dict(type="lounger",    model="/models/lounger_narrow.glb")),
    ("URN",        dict(type="urn",        model="/models/urn.glb")),
    ("BIKE_RACK",  dict(type="bike_rack",  model="/models/bike_rack.glb")),
    ("TRASH_BIN",  dict(type="trash_bin",  model="/models/trash_bin.glb")),
    ("LAMP",       dict(type="lamp",       model="/models/lamp.glb")),
    ("PLAYGROUND", dict(type="playground", model="/models/playground.glb")),
    ("ENTRANCE",   dict(type="entrance",   model="/models/entrance.glb")),
    ("CROSSWALK",  dict(type="crosswalk",  model="/models/crosswalk.glb")),
    # Русские названия (issue #50 follow-up): реальные DWG-проекты
    # Мосгеотреста размечают слои по-русски, не по-английски -- см. проверку
    # реального проекта "13_kharkovskaya" ("! ПР ДЕРЕВЬЯ", "ДВ_ГП_П_МАФ") и
    # docs/DWG_TO_DXF_INTEGRATION_GUIDE.md-эквивалентную разведку по
    # "07_peschany_pereulok" ("Фонари", "Отдельно стоящее дерево"). Порядок
    # значим меньше, чем для POLYGON_RULES -- extract_point_objects (ниже)
    # проверяет ВСЕ правила независимо, а не первое совпадение, поэтому
    # риск -- не порядок, а коллизия ключа с чужой категорией.
    ("ДЕРЕВ",      dict(type="tree",       model="/models/tree.glb")),
    ("КУСТ",       dict(type="bush",       model="/models/bush.glb")),
    ("СКАМ",       dict(type="bench",      model="/models/bench.glb")),
    ("ЛАВОЧ",      dict(type="bench",      model="/models/bench.glb")),
    ("УРН",        dict(type="urn",        model="/models/urn.glb")),
    ("ФОНАР",      dict(type="lamp",       model="/models/lamp.glb")),
    ("ВЕЛОПАРКОВ", dict(type="bike_rack",  model="/models/bike_rack.glb")),
]


def _species_point_rules() -> list[tuple[str, dict]]:
    """Каждый вид из справочника (data/plant_archetypes/species_catalog.json,
    232 записи) становится ОТДЕЛЬНЫМ правилом POINT_LAYER_RULES -- реальные
    проекты часто размечают точечную посадку не общим словом "дерево"/
    "кустарник", а конкретным русским названием вида на отдельном слое
    (например "! ПР ГОРТЕНЗИЯ МЕТЕЛЬЧАТАЯ", "! ПР СПИРЕЯ ВАНГУТТА" --
    подтверждено на реальном проекте "13_kharkovskaya"), и ни один keyword
    выше этого не поймает. tree/bush решается по полю category справочника;
    "Лианы" (вьющиеся) относим к bush -- отдельного типа для лиан в сцене
    нет, а по силуэту они ближе к кустарнику, чем к дереву. Молча
    возвращает [] при отсутствии/битости файла -- парсер не должен падать
    из-за необязательного справочника."""
    path = Path(__file__).resolve().parent.parent / "data" / "plant_archetypes" / "species_catalog.json"
    try:
        species = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    rules = []
    for entry in species:
        name = entry.get("name")
        category = entry.get("category", "")
        if not name:
            continue
        cfg = dict(type="tree", model="/models/tree.glb") if "дерев" in category.lower() \
            else dict(type="bush", model="/models/bush.glb")
        rules.append((name.upper(), cfg))
    return rules


POINT_LAYER_RULES = POINT_LAYER_RULES + _species_point_rules()

# Слой с 3D-мешами зданий (используется только для высоты)
BUILDING_MESH_LAYER_KEYWORDS = ["BUILDING", "ЗДАНИ"]

# Слои с фасадными элементами (3DFACE) -- не самостоятельные объекты сцены,
# а геометрия для отрисовки прямо на фасаде здания (см. extract_facade_quads).
FACADE_LAYER_KEYWORDS = {
    "windows": ["WINDOW"],
    "canopies": ["CANOPIES", "CANOPY"],
}

# Слои с бордюрами -- тоже не самостоятельные объекты (их сотни отрезков на
# участок, не переставляются), а линии для схематичной отрисовки прямо на
# земле (см. extract_curb_polylines). Тот же принцип, что у FACADE_LAYER_KEYWORDS
# выше, только 2D-полилинии вместо 3D-квадов.
CURB_LAYER_KEYWORDS = ["CURB", "KERB", "BORDER_STONE", "БОРТ", "БОРДЮР"]

# Единицы DXF ($INSUNITS) -> метры
INSUNITS_TO_METERS = {0: 1.0, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 0.9144}

# Диапазон, в который зажимается INSERT.xscale блока при использовании как
# render-scale нашей 3D-модели (issue #50 follow-up). xscale в исходном DWG
# отмасштабирован под РЕФЕРЕНСНУЮ ГЕОМЕТРИЮ ТОГО САМОГО блока (то, каким его
# нарисовал автор чертежа), а не под наши .glb-модели -- у них нет ничего
# общего по размеру. Подтверждено на реальном файле "13_kharkovskaya": блок
# "Яблоня 1" вставлен с xscale~0.0012 (референсная геометрия блока в тысячи
# раз крупнее дерева), другие деревья того же слоя -- xscale 0.69-2.0
# (правдоподобный, судя по всему НАМЕРЕННЫЙ разброс размера дерева от
# молодого до взрослого). Клампим, а не игнорируем xscale целиком, чтобы не
# потерять этот второй, осмысленный случай.
MIN_RENDER_SCALE = 0.3
MAX_RENDER_SCALE = 3.0


def _clamp_render_scale(xscale: float) -> float:
    return max(MIN_RENDER_SCALE, min(MAX_RENDER_SCALE, xscale))

# Максимальное отклонение (стрела прогиба) дуг HATCH-контура при аппроксимации
# отрезками (issue #50 follow-up) -- в исходных единицах DXF, ДО масштаба tf
# (см. extract_restrictions). План покрытий (газон/тротуар и т.п.) в реальных
# проектах Мосгеотреста часто залит HATCH-штриховкой, а не нарисован полигоном
# -- подтверждено на "13_kharkovskaya" (слои "...Газон_Рулонный"/"...трот..."
# состоят целиком из HATCH, ни одной LWPOLYLINE).
HATCH_FLATTENING_DISTANCE = 0.3


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
                xs.append(x)
                ys.append(y)
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


# Насколько близко должны сойтись концы склеенной linemerge-цепочки, чтобы
# считать контур реально замкнутым (issue #50 follow-up) -- тот же порог и
# та же причина, что у "реально замкнут vs разомкнутая топосъёмка" в ручной
# методике реконструкции зданий для этого же класса реальных DWG-проектов.
_BUILDING_CLOSE_GAP_M = 2.0
# Порог "это шум съёмки (забор, обрывок бордюра), а не здание" по короткой
# стороне прямоугольника-реконструкции -- тоже оттуда же.
_BUILDING_MIN_SHORT_SIDE_M = 4.0


def _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf):
    """Реальные топопланы Мосгеотреста часто рисуют контур здания сложным
    (штриховым) типом линии, который при конвертации DWG->DXF "взрывается" на
    тысячи отдельных LINE без какой-либо связи между собой в самом DXF
    (реальный случай, issue #50 follow-up: "13_kharkovskaya", слой "Здания" --
    1012 отдельных LINE, ни одной LWPOLYLINE). `shapely.linemerge` склеивает
    фрагменты по совпадающим концам обратно в непрерывные линии.

    Если склеенная цепочка сошлась в кольцо (концы ближе
    _BUILDING_CLOSE_GAP_M) -- берём её контур как есть, это надёжный случай.
    Если нет -- топосъёмка трассирует только видимую со стороны съёмки часть
    стен (тот же реальный эффект, что и с разомкнутыми LWPOLYLINE в другом
    проекте с этим же классом данных): честный convex hull на разомкнutой
    трассе часто вырождается в острый "парус", поэтому берём
    minimum_rotated_rectangle (честные 90° углы) и отбрасываем результат,
    если его короткая сторона меньше _BUILDING_MIN_SHORT_SIDE_M -- это шум
    съёмки, не здание."""
    for layer, (cfg, segments) in building_lines.items():
        if not segments:
            continue
        merged = linemerge([LineString(s) for s in segments])
        pieces = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
        for piece in pieces:
            coords = list(piece.coords)
            if len(coords) < 3:
                continue
            (x0, y0), (x1, y1) = coords[0], coords[-1]
            gap = math.hypot(x1 - x0, y1 - y0)
            if gap < _BUILDING_CLOSE_GAP_M:
                poly = Polygon(coords)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.geom_type != "Polygon" or poly.area <= 0:
                    continue
            else:
                hull = MultiPoint(coords).convex_hull
                if hull.geom_type != "Polygon":
                    continue
                rect = hull.minimum_rotated_rectangle
                rect_pts = list(rect.exterior.coords)[:-1]
                sides = [math.hypot(rect_pts[i][0] - rect_pts[i - 1][0], rect_pts[i][1] - rect_pts[i - 1][1]) for i in range(len(rect_pts))]
                if min(sides) < _BUILDING_MIN_SHORT_SIDE_M:
                    continue
                poly = rect
            pts3 = [(x, y, 0.0) for x, y in poly.exterior.coords[:-1]]
            _add_zone(zones, idx_by_type, cfg, layer, pts3, tf)


# Трассы сетей приходят из DXF раздробленными: одна линия кабеля -- это сотни
# отдельных LINE и незамкнутых полилиний (в реальном файле на 20 улиц: 73881
# LINE + 88369 сегментов полилиний). Раздувать каждый отрезок в собственную
# зону -- это 150 тысяч зон вместо нескольких сотен: 41 МБ JSON и примерно
# 450 тысяч объектов three.js на фронте, на которых браузер не укладывался и в
# 64 ГБ. Объединяем ПЕРЕД раздуванием, по слоям (слой = один тип сети с одними
# и теми же параметрами охранной зоны, их всего десяток на файл).
#
# Побочно это ещё и точнее: раздельные прямоугольники не накрывали клин на
# изломе трассы, а буфер цельной ломаной накрывает.
def _merge_corridors(zones, idx_by_type, corridors, tf):
    for layer, (cfg, lines) in corridors.items():
        if not lines:
            continue
        # Раздуваем каждую линию по отдельности и объединяем результат, а НЕ
        # буферизуем одну общую MultiLineString: на слое из 31820 кабельных
        # отрезков первое занимает 0.8 с, второе -- 8.7 с при том же итоге
        # (замерено). Векторизованный shapely.buffer обрабатывает массив
        # геометрий разом, а union_all внутри делает каскадное объединение,
        # тогда как буфер общей MultiLineString заставляет GEOS считать все
        # самопересечения трассы в одном проходе.
        #
        # quad_segs=2 вместо стандартных 8: скругление на изломе трассы -- деталь
        # порядка сантиметров на фоне охранной зоны в 2-3 метра, а вершин
        # экономит вчетверо.
        merged = shapely.union_all(
            shapely.buffer([LineString(c) for c in lines], cfg["minDistance"], cap_style=2, quad_segs=2)
        )
        if merged.is_empty:
            continue
        polys = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
        for poly in polys:
            # Дырки теряются: схема зоны -- плоский список точек без внутренних
            # контуров. Для запрета посадки это безопасная сторона ошибки
            # (закрытая дырка = чуть строже, чем есть на самом деле).
            # [:-1] -- shapely замыкает кольцо повтором первой точки, а в схеме
            # зоны полигон хранится незамкнутым (так же, как его отдаёт
            # polygon_points для обычных контуров).
            pts = list(poly.exterior.coords)[:-1]
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf)


# То же дробление, что чинит _merge_corridors выше, но для УЖЕ ЗАМКНУТЫХ
# контуров (газон, тротуарная плитка, дорожное полотно) -- рукописные
# тестовые DXF (locations/location_old/) отдавали такие слои одним
# полигоном на весь участок, поэтому раньше _add_zone вызывался прямо на
# каждый замкнутый контур без объединения. Реальные конвертированные файлы
# (locations/, issue #23) это предположение ломают: на 06_kamchatskaya_ulitsa
# один слой GRASS -- это 5751 отдельный контур (по куску газона на каждый
# фрагмент благоустройства в исходных данных), и после Этап 3/4 (см.
# site_characterization.py/zone_partitioning.py) с россыпью зон такого
# размера тем более не сработать: retrieval и разбиение на geometric-type
# зоны считают на ОДНОЙ объединённой площади, а не на тысяче стыкующихся
# осколков.
#
# Ту же порцию данных портит и низкое качество самой конвертации: заметная
# часть контуров на GRASS оказывается самопересекающейся или вырожденной
# (нулевая площадь) -- на 06_kamchatskaya_ulitsa валидных контуров с ненулевой
# площадью среди 5751 меньше 600. poly.buffer(0) чинит самопересечения тем же
# приёмом, что и building_setbacks.py; неисправимо вырожденные (buffer(0)
# всё равно пуст) отбрасываются молча -- это не потерянные данные, а
# нулевая площадь, вносить в JSON нечего.
#
# Простой union_all соседних кусков не склеивает: соседние плитки/сегменты
# дороги в исходнике сходятся не край-в-край, а с зазором в доли сантиметра
# (артефакт конвертации, не реальный разрыв) -- на слое ROAD того же файла
# 1630 валидных контуров, а после union_all остаётся 908, вместо ожидаемых
# нескольких десятков связных проездов. "Замыкание" (buffer наружу на
# _POLYGON_CLOSING_GAP_M, union, buffer обратно внутрь на ту же величину)
# перекрывает такие зазоры и даёт 134 -- при росте суммарной площади всего на
# 0.9% (замерено на том же файле/слое). Величина зазора -- сантиметр, на
# порядок меньше любого нормативного отступа в setback_norms.py, поэтому
# приклеить два физически разных объекта эта операция не может.
#
# quad_segs=4 (не дефолтные 8) -- та же экономия, что и в _merge_corridors,
# и тем более нужна здесь: слоёв тут сотни-тысячи полигонов (GRASS на
# 06_kamchatskaya_ulitsa -- 5751), и unary_union после buffer(quad_segs=8)
# на них уходил в 5-10 раз дольше при не сильно лучшем результате (замерено:
# quad_segs=8 -- 433 итоговых полигона за 5.7с, quad_segs=4 -- 608 за 0.65с
# на том же слое). Буферим ВЕКТОРИЗОВАННЫМ shapely.buffer(list, ...), а не
# циклом p.buffer(...) -- тот же приём, что в _merge_corridors, там же и
# объяснение, почему это быстрее.
_POLYGON_CLOSING_GAP_M = 0.01
_POLYGON_CLOSING_QUAD_SEGS = 4


def _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf):
    for layer, (cfg, polys) in polygon_zones.items():
        if not polys:
            continue
        buffered = shapely.buffer(polys, _POLYGON_CLOSING_GAP_M, quad_segs=_POLYGON_CLOSING_QUAD_SEGS)
        merged = shapely.union_all(buffered).buffer(
            -_POLYGON_CLOSING_GAP_M, quad_segs=_POLYGON_CLOSING_QUAD_SEGS
        )
        if merged.is_empty:
            continue
        result_polys = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
        for poly in result_polys:
            # Дырки теряются -- тот же компромисс, что в _merge_corridors, и по
            # той же причине (схема зоны не хранит внутренние контуры).
            pts = list(poly.exterior.coords)[:-1]
            if len(pts) >= 3:
                _add_zone(zones, idx_by_type, cfg, layer, pts, tf)


# Порог площади куска "открытой земли" после вычитания всех известных зон --
# меньше отбрасываем как обрывок геометрии (погрешность буфера/пересечения
# на стыке зон), не настоящий плантируемый кусок.
_GROUND_ZONE_MIN_AREA_SQM = 5.0
# Имя источника у автоматически вычисленной зоны -- по нему GreenPlan/фронтенд
# может отличить её от зоны, реально найденной по слою DXF (issue #53).
GROUND_ZONE_SOURCE_NAME = "__computed_ground__"


def _compute_ground_zone(boundary, restrictions):
    """Недостающая "открытая земля" = граница участка (настоящая или
    оценённая, см. _estimate_boundary_from_content) минус объединение ВСЕХ
    уже известных restriction-зон -- включая уже допустимые газоны (issue
    #53). Там, где явный GROUND-слой/газон уже покрывает весь участок
    (вручную собранные locations/), остаток естественно получается пустым
    или незначительным -- ничего не дублируется. Там, где плана покрытий нет
    вообще или он покрывает участок не полностью (реальные DWG-батчи,
    issue #50), остаток становится новой allowed-зоной -- тот же смысл, что
    у ключевого слова GROUND в POLYGON_RULES выше, просто найдено
    геометрически, а не по имени слоя (см. GreenPlan/generate-greenery,
    которые сажают ТОЛЬКО внутри severity=allowed -- без этой зоны
    неклассифицированная открытая земля молча оставалась без посадки, хотя
    визуально места было много).

    Работает уже в ВЫХОДНЫХ координатах сцены (restrictions/boundary сюда
    приходят уже трансформированными -- в отличие от _add_zone выше, здесь
    NO tf.polygon() второй раз, иначе координаты исказились бы)."""
    if boundary is None or len(boundary["polygon"]) < 3:
        return []
    boundary_poly = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not boundary_poly.is_valid:
        boundary_poly = boundary_poly.buffer(0)
    if boundary_poly.is_empty or boundary_poly.area == 0:
        return []

    known_polys = []
    for zone in restrictions:
        if len(zone["polygon"]) < 3:
            continue
        poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty and poly.area > 0:
            known_polys.append(poly)

    remaining = boundary_poly if not known_polys else boundary_poly.difference(shapely.union_all(known_polys))
    if remaining.is_empty:
        return []

    parts = remaining.geoms if remaining.geom_type == "MultiPolygon" else [remaining]
    existing_count = sum(1 for z in restrictions if z["type"] == "protected_zone")

    result = []
    for part in parts:
        if part.geom_type != "Polygon" or part.area < _GROUND_ZONE_MIN_AREA_SQM:
            continue
        pts = list(part.exterior.coords)[:-1]
        if len(pts) < 3:
            continue
        existing_count += 1
        result.append({
            "id": f"protected_zone_{existing_count:03d}",
            "type": "protected_zone",
            "name": GROUND_ZONE_SOURCE_NAME,
            "polygon": [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts],
            "severity": "allowed",
            "minDistance": 0.0,
            "message": "Открытая земля — вычислено как участок минус здания/дорога/тротуар",
        })
    return result


# Максимальная суммарная "дальнобойность" зоны от её собственной геометрии:
# minDistance при разметке (максимум в POLYGON_RULES -- 3.0, sewer/water) +
# отступ по виду посадки, добавляемый ПОЗЖЕ генератором (максимум в
# setback_norms.SETBACK_NORMS -- 5.0, building/tree) = 8.0 м в худшем случае.
# Дальше зона участка вообще не касается ни при какой посадке. Берём с
# запасом в 2.5 раза, а не впритык -- граница не всегда идеально ровная
# (углы, выступы), и обрезка не обязана быть хирургически точной, ей важно
# отсечь именно общегородскую подложку в сотнях метров, а не сэкономить
# сантиметры у самого края.
_RESTRICTION_RELEVANCE_MARGIN_M = 20.0


def _clip_offsite_zones(zones, boundary):
    """Обрезает зоны по _RESTRICTION_RELEVANCE_MARGIN_M от границы участка --
    см. докстринг boundary в extract_restrictions про то, откуда берётся
    посторонняя геометрия. Здания не трогаем: см. там же.

    ОБРЕЗАЕТ, а не просто отбрасывает целиком: сеть на общегородской
    подложке -- это один непрерывный коридор в сотни метров, который своим
    небольшим куском всё же заходит в разрешённую область (иначе не было бы
    смысла его вообще учитывать) -- intersects() на всей зоне был бы True, и
    ничего бы не отсеялось, хотя 95% её площади к участку отношения не
    имеет. intersection() с регионом оставляет только релевantный кусок,
    остальное просто не входит в результат."""
    if boundary is None or len(boundary["polygon"]) < 3:
        return zones
    site = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    if not site.is_valid or site.area == 0:
        return zones
    region = site.buffer(_RESTRICTION_RELEVANCE_MARGIN_M)

    result = []
    for zone in zones:
        if zone["type"] == "building" or len(zone["polygon"]) < 3:
            result.append(zone)
            continue
        poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
        if not poly.is_valid or region.contains(poly):
            result.append(zone)
            continue
        clipped = poly.intersection(region)
        if clipped.is_empty:
            continue
        parts = clipped.geoms if clipped.geom_type == "MultiPolygon" else [clipped]
        for part in parts:
            pts = list(part.exterior.coords)[:-1]
            if len(pts) < 3:
                continue
            new_zone = dict(zone)
            new_zone["polygon"] = [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts]
            result.append(new_zone)

    # Переприсваиваем id заново по типу -- клип мог развалить одну зону на
    # несколько кусков (MultiPolygon), старые id из _add_zone на них не
    # годятся (либо дубли, либо пропуски).
    idx_by_type = Counter()
    for zone in result:
        idx_by_type[zone["type"]] += 1
        zone["id"] = f"{zone['type']}_{idx_by_type[zone['type']]:03d}"
    return result


# "Дальний выброс" по Тьюки -- за пределами IQR*_OUTLIER_IQR_FACTOR от
# межквартильного размаха (3.0 -- стандартный порог именно для "дальних", а
# не любых, выбросов, вдвое строже обычных 1.5, используемых для разметки
# отдельных точек на boxplot). Работает независимо от размера выборки, в
# отличие от прямого перцентильного индекса (int(n*0.01) даёт 0 при n<100,
# т.е. не отбрасывает вообще ничего на типичных сценах в сотни объектов).
_OUTLIER_IQR_FACTOR = 3.0
_OUTLIER_MIN_MARGIN_M = 50.0
_OUTLIER_MIN_POINTS = 10


def _robust_bounds(values):
    """Границы Тьюки [Q1 - k*IQR, Q3 + k*IQR] по отсортированному списку --
    устойчивы к единичным дальним выбросам, в отличие от честного min/max.
    Запас снизу ограничен _OUTLIER_MIN_MARGIN_M, чтобы не обрезать
    естественный разброс плотного скопления с маленьким IQR."""
    n = len(values)
    q1 = values[n // 4]
    q3 = values[(3 * n) // 4]
    margin = max((q3 - q1) * _OUTLIER_IQR_FACTOR, _OUTLIER_MIN_MARGIN_M)
    return q1 - margin, q3 + margin


def _estimate_fallback_region(objects, restrictions, curbs):
    """Прямоугольная оценка "относящейся к площадке" области, когда явного
    слоя границы участка нет вообще (см. docstring boundary в
    extract_restrictions про то, откуда берётся посторонняя геометрия).
    Без границы _clip_offsite_zones ничего не отсеивает, и единичная точка,
    случайно дотянутая из общегородской подложки/чужого тайла, растягивает
    bbox всей сцены в разы -- настоящая, корректно отмасштабированная
    посадка выглядит на экране крошечной точкой на фоне пустоты (реальный
    случай, issue #50 follow-up: "13_kharkovskaya" -- 99% из 912 деревьев/
    кустов укладывались в область ~700×200м, но одно-два дерева оказались в
    8-9 км от неё).

    Границы Тьюки по X и Z ОТДЕЛЬНО по всем собранным координатам сразу
    (объекты + вершины зон + вершины бордюров) -- честно длинный узкий
    участок (набережная, проезд) не обрезается по краям, потому что его
    точки распределены непрерывно, без разрыва, и попадают в межквартильный
    размах целиком. Возвращает None, если точек слишком мало для устойчивой
    оценки (тогда ничего не фильтруем)."""
    xs, zs = [], []
    for o in objects:
        xs.append(o["position"]["x"])
        zs.append(o["position"]["z"])
    for r in restrictions:
        for p in r["polygon"]:
            xs.append(p["x"])
            zs.append(p["z"])
    for c in curbs:
        for p in c:
            xs.append(p["x"])
            zs.append(p["z"])

    if len(xs) < _OUTLIER_MIN_POINTS:
        return None

    xs.sort()
    zs.sort()
    minx, maxx = _robust_bounds(xs)
    minz, maxz = _robust_bounds(zs)
    return (minx, maxx, minz, maxz)


def _point_in_region(x, z, region):
    minx, maxx, minz, maxz = region
    return minx <= x <= maxx and minz <= z <= maxz


def _region_to_polygon_points(region):
    minx, maxx, minz, maxz = region
    return [{"x": minx, "z": minz}, {"x": maxx, "z": minz}, {"x": maxx, "z": maxz}, {"x": minx, "z": maxz}]


# Запас вокруг concave hull содержимого при оценке границы участка -- чтобы
# не терять объекты у самого края из-за погрешности аппроксимации.
_ESTIMATED_BOUNDARY_MARGIN_M = 10.0
# ratio для shapely.concave_hull: 0 -- максимально детальный контур (ближе к
# альфа-форме), 1 -- обычный convex hull. 0.2 -- компромисс между точностью
# формы участка (важно для plantable_ratio/elongation в GreenPlan, см.
# _estimate_boundary_from_content) и устойчивостью/числом вершин контура.
_ESTIMATED_BOUNDARY_CONCAVITY_RATIO = 0.2
# По этой метке GreenPlan/фронтенд может отличить вычисленную границу от
# реально найденного в DXF слоя -- это ОЦЕНКА, не официальный кадастр.
ESTIMATED_BOUNDARY_SOURCE_LAYER = "__estimated_from_content__"


def _estimate_boundary_from_content(objects, restrictions, curbs):
    """Граница участка "по факту" -- concave hull уже отфильтрованной (см.
    _estimate_fallback_region) геометрии сцены, когда явного слоя границы в
    DXF нет вообще (issue #50 follow-up). Это не косметика: GreenPlan
    (backend/site_characterization.py::characterize_site,
    backend/zone_partitioning.py::partition_zones) требует scene.boundary
    ЖЁСТКО -- обе функции возвращают None/[] при boundary=None, ничего не
    пытаясь сделать с одними restrictions/objects. Реальный случай: сцена с
    904 объектами и 15355 зонами (issue #50, "13_kharkovskaya" с топопланом)
    давала 0 assignments в /api/greenplan/generate именно поэтому, а не
    из-за нехватки места, как выглядело со стороны ("места много, а не
    расставилось").

    Concave hull, а не bbox/convex hull: у вытянutой улицы bbox/hull сильно
    завышают total_area против реальной формы участка, искажая
    plantable_ratio и elongation, на которых строится классификация
    territory_type. Небольшой запас (_ESTIMATED_BOUNDARY_MARGIN_M) по краям,
    чтобы не обрезать объекты впритык к контуру из-за погрешности
    аппроксимации. Возвращает None при недостатке точек для устойчивой
    оценки формы -- тогда сцена остаётся без границы, как и раньше."""
    points = [(o["position"]["x"], o["position"]["z"]) for o in objects]
    for r in restrictions:
        for p in r["polygon"]:
            points.append((p["x"], p["z"]))
    for c in curbs:
        for p in c:
            points.append((p["x"], p["z"]))

    if len(points) < _OUTLIER_MIN_POINTS:
        return None

    multipoint = MultiPoint(points)
    try:
        # GEOS-триангуляция внутри concave_hull иногда падает на почти
        # вырожденных наборах точек (например, объекты почти на одной
        # прямой -- узкий вытянутый участок с малым числом объектов) --
        # "Tri::getAdjacent - invalid index", воспроизведено на 12 точках
        # вдоль прямой. convex_hull не строит триангуляцию Делоне вообще,
        # поэтому устойчив там, где concave_hull ломается -- ценой более
        # грубой формы (это всё равно лучше, чем совсем без границы).
        hull = concave_hull(multipoint, ratio=_ESTIMATED_BOUNDARY_CONCAVITY_RATIO)
    except shapely.errors.GEOSException:
        hull = multipoint.convex_hull
    hull = hull.buffer(_ESTIMATED_BOUNDARY_MARGIN_M)
    if hull.geom_type != "Polygon" or not hull.is_valid or hull.area <= 0:
        return None

    pts = list(hull.exterior.coords)[:-1]
    if len(pts) < 3:
        return None
    return {
        "polygon": [{"x": round(x, 3), "z": round(z, 3)} for x, z in pts],
        "sourceLayer": ESTIMATED_BOUNDARY_SOURCE_LAYER,
    }


def extract_restrictions(msp, tf, boundary=None):
    """Закрытые LWPOLYLINE/POLYLINE -> зона-полигон, объединённая по слою
    (см. _merge_polygon_zones) -- НЕ как есть по одной сущности, вопреки тому,
    что можно было бы предположить по рукописным тестовым файлам
    (locations/location_old/), где такой слой всегда ровно один полигон на
    весь участок. Реальные конвертированные DXF (locations/) это ломают:
    один слой газона может прийти тысячами мелких кусков.

    LINE и открытые (не замкнутые) POLYLINE -> трасса трубы/кабеля. Все трассы
    ОДНОГО СЛОЯ собираются вместе и раздуваются в коридоры шириной 2*minDistance
    одним буфером с объединением (см. _merge_corridors), а не по зоне на
    сущность и тем более не по зоне на звено ломаной.

    Дробление тут било трижды. По звену ломаной -- это десятки тысяч мелких зон
    на плотно оцифрованной сети, непрактично медленно для unary_union в
    generate-greenery, плюс щели и наслоения на изгибах вместо гладкого
    коридора. По сущности -- уже лучше, но конвертер выдаёт трассу разрезанной
    на сотни отдельных полилиний (17745 штук на участок в 20 улиц), и зон всё
    равно оставались тысячи. По уже ЗАМКНУТОМУ контуру (газон, плитка, дорога)
    -- третий случай, тот же симптом на других слоях (см. _merge_polygon_zones).
    Объединение по слою закрывает все три случая разом: слой -- это один тип
    зоны с одними и теми же параметрами охранной зоны.

    Строго вертикальные участки (стояки-подключения к зданию, где меняется
    только Z) пропускаются -- это не горизонтальное ограничение в плане XZ.

    boundary -- уже посчитанная extract_boundary() граница участка (в
    масштабе сцены, тем же tf). Если передана, зоны обрезаются по
    _RESTRICTION_RELEVANCE_MARGIN_M от неё в самом конце (см.
    _clip_offsite_zones) -- реальные конвертированные файлы (не рукописные
    тестовые) тянут инженерные сети из общегородской подложки без обрезки по
    границе: на 11_frunzenskaya_naberezhnaya и 13_kharkovsky_proezd (оба --
    узкие вытянутые участки, набережная и проезд) сети покрывают полосу в
    сотни метров ПО ОБЕ СТОРОНЫ от участка, и из-за узкой формы самого
    участка их отступы перекрывают его насквозь -- generate_trees находил
    место для 2 и 0 деревьев соответственно на гектарах площади. Здания в
    фильтр не попадают: сколько бы сеть ни простиралась за границу, здание
    вне участка -- это не полезная зона ограничения, а посторонний объект,
    filtering для них не нужен (в этих файлах их и не оказалось за
    границей)."""
    zones = []
    idx_by_type = Counter()
    corridors = {}
    polygon_zones = {}
    building_lines = {}

    def add_line(layer, cfg, coords):
        if len(coords) < 2:
            return
        # Здания из слияния исключены намеренно. Оно рассчитано на сети --
        # непрерывные трассы, которые и в реальности одна сущность. Здания же
        # дискретны, а extract_buildings ниже строит объекты сцены ИЗ этих зон:
        # два соседних дома ближе 2*minDistance слиплись бы в один полигон и
        # дальше в одно здание. Отрезок на слое здания остаётся отдельной зоной,
        # как было до слияния.
        if cfg["type"] == "building":
            for a, b in zip(coords, coords[1:]):
                seg = buffer_segment(a[0], a[1], b[0], b[1], cfg["minDistance"])
                if seg:
                    _add_zone(zones, idx_by_type, cfg, layer, seg, tf)
            return
        corridors.setdefault(layer, (cfg, []))[1].append(coords)

    def add_closed_polygon(layer, cfg, pts):
        # Здания -- та же причина, что и в add_line: по одному объекту сцены
        # на здание (extract_buildings), слияние соседних домов в один
        # полигон превратило бы два дома в один.
        if cfg["type"] == "building":
            _add_zone(zones, idx_by_type, cfg, layer, pts, tf)
            return
        poly = Polygon([(x, y) for x, y, *_ in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            return
        polygon_zones.setdefault(layer, (cfg, []))[1].append(poly)

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
                add_closed_polygon(layer, cfg, pts)
        else:
            # Ломаная режется на куски только там, где идёт чисто вертикальный
            # стояк: он не ограничение в плане, но и склеивать через него
            # соседние участки в одну прямую нельзя -- получилась бы трасса,
            # которой нет.
            run = []
            for (x1, y1, _z1), (x2, y2, _z2) in zip(pts, pts[1:]):
                if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                    add_line(layer, cfg, run)
                    run = []
                    continue
                if not run:
                    run.append((x1, y1))
                run.append((x2, y2))
            add_line(layer, cfg, run)

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
        # Здания -- отдельно от общего add_line (issue #50 follow-up): в
        # реальных топопланах контур здания часто рисуется сложным
        # (штриховым) линтайпом, который при конвертации DWG->DXF "взрывается"
        # на тысячи отдельных LINE безо всякой связи между собой в самом DXF
        # (реальный случай, "13_kharkovskaya": слой "Здания" -- 1012
        # отдельных LINE, ни одной LWPOLYLINE). add_line для зданий буферизует
        # КАЖДЫЙ отрезок отдельно -- на несвязанных фрагментах превратил бы
        # одно здание в тысячу несвязанных полосок. Копим по слою, склеиваем
        # в цельные контуры позже, см. _reconstruct_buildings_from_line_fragments.
        if cfg["type"] == "building":
            building_lines.setdefault(layer, (cfg, []))[1].append(((s.x, s.y), (en.x, en.y)))
            continue
        add_line(layer, cfg, [(s.x, s.y), (en.x, en.y)])

    # HATCH -- план покрытий (газон/тротуар и т.п.) в реальных DWG-проектах
    # часто залит штриховкой, а не нарисован полигоном (issue #50 follow-up,
    # см. HATCH_FLATTENING_DISTANCE выше). Каждый boundary path заливки --
    # свой замкнутый контур; ezdxf.path.from_hatch отдаёт их все разом
    # (внешний контур + возможные острова-дыры вперемешку, без явного флага
    # "это дыра") -- как и справочный пример, из которого это переписано, не
    # различаем их и просто объединяем все контуры слоя через ту же
    # add_closed_polygon/_merge_polygon_zones, что и обычные полигоны: на
    # практике эти покрытия почти всегда без внутренних вырезов, а island-
    # контур (если и встретится) просто добавит лишний маленький кусок той же
    # разрешённой зоны, не испортит форму в целом.
    for e in msp.query("HATCH"):
        layer = e.dxf.layer
        if layer_matches(layer, BOUNDARY_LAYER_KEYWORDS) or layer_matches(layer, SKIP_LAYER_KEYWORDS):
            continue
        cfg = match_rule(layer, POLYGON_RULES)
        if not cfg:
            continue
        for p in ezpath.from_hatch(e):
            pts = [(v.x, v.y, v.z) for v in p.flattening(distance=HATCH_FLATTENING_DISTANCE)]
            if len(pts) >= 3:
                add_closed_polygon(layer, cfg, pts)

    _reconstruct_buildings_from_line_fragments(zones, idx_by_type, building_lines, tf)
    _merge_corridors(zones, idx_by_type, corridors, tf)
    _merge_polygon_zones(zones, idx_by_type, polygon_zones, tf)
    return _clip_offsite_zones(zones, boundary)


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
                "metadata": {"blockName": getattr(e.dxf, "name", None), "sourceLayer": e.dxf.layer},
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
                    "metadata": {"height": round(top_z * tf.scale, 2), "sourceLayer": ln.dxf.layer},
                })

        # Одиночные CIRCLE без пары LINE -- просто маркер точки (напр. дверь
        # подъезда на слое ENTRANCES), а не фонарный столб. Та же правка, что
        # и выше: не требуем "and not inserts_or_points".
        elif circles and not lines:
            for c in circles:
                counters[cfg["type"]] += 1
                objects.append({
                    "id": f"{cfg['type']}_{counters[cfg['type']]:03d}",
                    "type": cfg["type"],
                    "model": cfg["model"],
                    "position": tf.point(c.dxf.center.x, c.dxf.center.y, c.dxf.center.z),
                    "rotation": 0,
                    "scale": 1,
                    "metadata": {"sourceLayer": c.dxf.layer},
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

def parse_dxf_doc(doc, scale=None, center=True):
    """Разобрать уже открытый ezdxf-документ в {boundary, restrictions, objects, meta}.
    Общее ядро для CLI (main()) и для backend/main.py (веб-эндпоинт /api/parse) —
    оба должны парсить одинаково, поэтому вся логика тут, а не продублирована."""
    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS", 0)
    resolved_scale = scale if scale is not None else INSUNITS_TO_METERS.get(insunits, 1.0)

    origin_x, origin_y = 0.0, 0.0
    if center:
        for e in msp.query("LWPOLYLINE POLYLINE"):
            if layer_matches(e.dxf.layer, BOUNDARY_LAYER_KEYWORDS):
                pts = polygon_points(e)
                if pts:
                    origin_x, origin_y = centroid(pts)
                break

    tf = Transform(scale=resolved_scale, origin_x=origin_x, origin_y=origin_y)

    boundary = extract_boundary(msp, tf)
    restrictions = extract_restrictions(msp, tf, boundary)
    buildings = extract_buildings(msp, tf, restrictions)
    points = extract_point_objects(msp, tf)
    objects = buildings + points
    facade = extract_facade_quads(msp, tf)
    curbs = extract_curb_polylines(msp, tf)

    # Без явного слоя границы (issue #50 follow-up: реальные DWG-батчи без
    # слоя ГРАНИЦА -- см. предупреждение в docstring extract_restrictions про
    # boundary про то, откуда берётся посторонняя геометрия) ничего выше не
    # отсеивает единичные точки, случайно дотянутые из общегородской
    # подложки/чужого тайла: одна такая точка растягивает bbox сцены в разы,
    # и настоящая, корректно отмасштабированная посадка выглядит на экране
    # крошечной точкой на фоне пустоты (реальный случай -- "13_kharkovskaya":
    # 99% из 912 деревьев/кустов укладывались в область ~700×200м, но одно-два
    # дерева оказались в 8-9 км от неё). Оцениваем плотное ядро координат и
    # отсеиваем/обрезаем всё, что снаружи -- с реальной границей это уже
    # делает _clip_offsite_zones для restrictions, здесь то же самое, но для
    # всех трёх коллекций сразу и по оценке, а не по границе.
    if boundary is None:
        region = _estimate_fallback_region(objects, restrictions, curbs)
        if region is not None:
            objects = [o for o in objects if _point_in_region(o["position"]["x"], o["position"]["z"], region)]
            fake_boundary = {"polygon": _region_to_polygon_points(region)}
            restrictions = _clip_offsite_zones(restrictions, fake_boundary)
            curbs = [c for c in curbs if all(_point_in_region(p["x"], p["z"], region) for p in c)]

        # Без границы GreenPlan (characterize_site/partition_zones) не
        # работает вообще -- см. docstring _estimate_boundary_from_content.
        # Считаем её ПОСЛЕ отсева выбросов выше, чтобы редкая дальняя точка
        # не растянула и сам контур.
        boundary = _estimate_boundary_from_content(objects, restrictions, curbs)

    # Открытая земля (issue #53) -- участок минус всё уже известное. Не
    # только для сцен без явного слоя границы: план покрытий (газон/тротуар)
    # в реальных DWG-проектах часто не покрывает весь участок, даже когда
    # сама граница найдена по слою -- см. docstring _compute_ground_zone.
    restrictions = restrictions + _compute_ground_zone(boundary, restrictions)

    return {
        "boundary": boundary,
        "restrictions": restrictions,
        "objects": objects,
        "windows": facade["windows"],
        "canopies": facade["canopies"],
        "curbs": curbs,
        "meta": {
            "scale": resolved_scale,
            "insunits": insunits,
            "origin": {"x": origin_x, "y": origin_y},
            "buildingCount": len(buildings),
            "pointObjectCount": len(points),
        },
    }


def parse_dxf_file(path, scale=None, center=True):
    doc = ezdxf.readfile(path)
    return parse_dxf_doc(doc, scale=scale, center=center)


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
    except OSError:
        sys.exit(f"Не удалось открыть файл: {args.input}")
    except ezdxf.DXFStructureError:
        sys.exit(f"Файл повреждён или не является корректным DXF: {args.input}")

    if args.summary:
        print_summary(doc)
        return

    result = parse_dxf_doc(doc, scale=args.scale, center=not args.no_center)
    boundary, restrictions, objects, meta = (
        result["boundary"], result["restrictions"], result["objects"], result["meta"])
    buildings = [o for o in objects if o["type"] == "building"]
    points = [o for o in objects if o["type"] != "building"]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "boundary.json").write_text(
        json.dumps(boundary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "restrictions.json").write_text(
        json.dumps(restrictions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "objects.json").write_text(
        json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "facade.json").write_text(
        json.dumps({"windows": result["windows"], "canopies": result["canopies"]},
                    ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "curbs.json").write_text(
        json.dumps(result["curbs"], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Масштаб: {meta['scale']} м/ед. (INSUNITS={meta['insunits']}), "
          f"origin=({meta['origin']['x']:.2f}, {meta['origin']['y']:.2f})")
    print(f"boundary.json      — {'1 полигон' if boundary else 'не найден'}")
    print(f"restrictions.json  — {len(restrictions)} зон")
    print(f"objects.json       — {len(objects)} объектов ({len(buildings)} зданий, {len(points)} точечных)")
    print(f"facade.json        — {len(result['windows'])} окон, {len(result['canopies'])} граней козырьков")
    print(f"curbs.json         — {len(result['curbs'])} бордюров")
    print(f"\nЗаписано в {out_dir.resolve()}")


if __name__ == "__main__":
    main()
