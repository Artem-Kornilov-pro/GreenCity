"""
Конфигурация распознавания слоёв DXF: какие подстроки в имени слоя дают
какой тип зоны ограничения (POLYGON_RULES) или точечного объекта
(POINT_LAYER_RULES), плюс ключевые слова границы, фасадов, бордюров и
единицы измерения. Сопоставление -- по подстроке без учёта регистра, в
порядке списка: побеждает первое совпадение.
"""

import json
from pathlib import Path

# Слои-многоугольники -> зоны ограничений (RestrictionZone). Порядок значим:
# специфичные ключи (OVERHEAD, CABLE_COMM) стоят раньше общих (POWER, ELECTR).
POLYGON_RULES = [
    # minDistance -- отступ для кустарника; отступы по виду посадки и породе
    # считает backend/core/setback_norms.py.
    ("BUILDING",    dict(type="building",            severity="forbidden", minDistance=1.5,
                          message="Отступ от здания: дерево — 5 м, кустарник — 1.5 м")),
    ("TRANSFORMER", dict(type="transformer",          severity="forbidden", minDistance=2.0, message="Трансформаторная подстанция")),
    ("ROAD",        dict(type="road",                 severity="forbidden", minDistance=1.0, message="Дорожное полотно — посадка запрещена")),
    ("PARK",        dict(type="custom",               severity="warning",   minDistance=1.0, message="Зона парковки")),
    ("PLAYGROUND",  dict(type="playground_zone",      severity="warning",   minDistance=1.0, message="Детская площадка")),
    ("GAS",         dict(type="gas_pipeline",         severity="forbidden", minDistance=2.0, message="Охранная зона газопровода")),
    ("SEWER",       dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона канализации")),
    ("WATER",       dict(type="water_pipeline",       severity="forbidden", minDistance=3.0, message="Охранная зона водопровода")),
    # Кабель связи -- слаботочная сеть: охранная зона уже, чем у силового
    # кабеля, и посадку она не запрещает. Стоит раньше ELECTR/CABLE: слой
    # ELECTR_CABLE_COMM содержит и их.
    ("CABLE_COMM",  dict(type="signal_cable",         severity="warning",   minDistance=0.5,
                          message="Кабель связи — слаботочная сеть, охранная зона уже, чем у силового кабеля")),
    ("ELECTR",      dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электросети")),
    # Воздушная ЛЭП: под проводом сажать можно, ограничена только высота.
    ("OVERHEAD",    dict(type="overhead_power_line",  severity="warning",   minDistance=2.0, maxHeight=4.0,
                          message="Наземная ЛЭП — ограничение по высоте посадки под проводом (~9м), не запрет на посадку как таковую")),
    ("POWER",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    ("CABLE",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    # У теплосети своя строка в таблице отступов и правила по породе
    # (backend/core/setback_norms.py).
    ("HEAT",        dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("WALKWAY",     dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PATH",        dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("SIDEWALK",    dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка")),
    ("PROTECT",     dict(type="protected_zone",       severity="forbidden", minDistance=1.0, message="Охраняемая зона")),
    ("GRASS",       dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
    ("LAWN",        dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
    # Открытая земля -- граница участка минус здания, дороги и тротуары,
    # вычисляется при конвертации DWG. Как и газон, это место под посадку.
    ("GROUND",      dict(type="protected_zone",       severity="allowed",   minDistance=0.0,
                          message="Открытая земля — вычислено как участок минус здания/дорога/тротуар")),

    # Русские названия слоёв проектов Мосгеотреста (топоплан, подземные
    # сети, проектное решение). Тот же порядок: узкое правило раньше общего.
    ("ЗДАНИ",       dict(type="building",             severity="forbidden", minDistance=1.5,
                          message="Отступ от здания: дерево — 5 м, кустарник — 1.5 м")),
    ("ГАЗОПРОВОД",  dict(type="gas_pipeline",         severity="forbidden", minDistance=2.0, message="Охранная зона газопровода")),
    ("КАНАЛИЗАЦ",   dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона канализации")),
    ("ВОДОСТОК",    dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона водостока")),
    ("ДРЕНАЖ",      dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона дренажа")),
    ("КОЛЛЕКТОР",   dict(type="sewer",                severity="forbidden", minDistance=3.0, message="Охранная зона общего коллектора коммуникаций")),
    ("ВОДОПРОВОД",  dict(type="water_pipeline",       severity="forbidden", minDistance=3.0, message="Охранная зона водопровода")),
    ("СВЯЗ",        dict(type="signal_cable",         severity="warning",   minDistance=0.5,
                          message="Кабель связи — слаботочная сеть, охранная зона уже, чем у силового кабеля")),
    ("ТЕПЛОСЕТ",    dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    # Так теплосеть называют нормативы (743-ПП, МГСН 1.02-02).
    ("ТЕПЛОТРАСС",  dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("ТЕПЛОПРОВОД", dict(type="heat_network",         severity="forbidden", minDistance=2.0, message="Охранная зона теплосети")),
    ("ЛЭП",         dict(type="overhead_power_line",  severity="warning",   minDistance=2.0, maxHeight=4.0,
                          message="Наземная ЛЭП — ограничение по высоте посадки под проводом (~9м), не запрет на посадку как таковую")),
    ("ЭЛЕКТР",      dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электросети")),
    ("КАБЕЛ",       dict(type="electrical",           severity="forbidden", minDistance=2.0, message="Охранная зона электрокабеля")),
    # Неуточнённые подземные сети -- после всех конкретных видов сетей.
    ("ПОДЗЕМН",     dict(type="custom",               severity="forbidden", minDistance=2.0, message="Неуточнённые подземные коммуникации")),
    ("ТРОТ",        dict(type="pedestrian_path",      severity="warning",   minDistance=0.5, message="Пешеходная дорожка/тротуар")),
    ("УЛИЦ",        dict(type="road",                 severity="forbidden", minDistance=1.0, message="Проезжая часть улицы — посадка запрещена")),
    ("ГАЗОН",       dict(type="protected_zone",       severity="allowed",   minDistance=0.0, message="Газон — допустимая зона озеленения")),
]

# Слои границы участка. Русские -- стандартные слои границы работ в
# проектах; «Граница улицы» и «Граница площадки» сюда не входят.
PROJECT_BOUNDARY_LAYER_KEYWORDS = ["ГРАНИЦА РАБОТ", "ГРАНИЦЫ РАБОТ", "ГРАНИЦА ПРОЕКТИРОВАНИЯ", "ГРАНИЦА БЛАГОУСТРОЙСТВА"]
BOUNDARY_LAYER_KEYWORDS = ["BOUNDARY", "TERRITORY", "SITE", *PROJECT_BOUNDARY_LAYER_KEYWORDS]

# Слои без структурных данных -- пропускаются. «Граница улицы» топоплана --
# линия, а не проезжая часть: она приходит тысячами замкнутых обрывков, и
# правило «УЛИЦ» сделало бы каждый запретной зоной.
SKIP_LAYER_KEYWORDS = ["MARKING", "LABEL", "DIM", "TEXT", "ГРАНИЦА УЛИЦ"]

# Слои, совпадающие с POINT_LAYER_RULES по подстроке, но не точечные объекты:
# «Полоса деревьев» топоплана -- контур полосы растительности из тысяч
# отрезков, а не дерево на каждую точку.
POINT_LAYER_EXCLUDE_KEYWORDS = ["ПОЛОСА"]

# Точечные объекты -> SceneObject. Ключ -- подстрока имени слоя.
POINT_LAYER_RULES = [
    ("TREE",       dict(type="tree",       model="/models/tree.glb")),
    ("BUSH",       dict(type="bush",       model="/models/bush.glb")),
    ("SHRUB",      dict(type="bush",       model="/models/bush.glb")),
    ("BENCH",      dict(type="bench",      model="/models/bench.glb")),
    # Шезлонг -- отдельный от скамейки тип со своей моделью.
    ("LOUNGER",    dict(type="lounger",    model="/models/lounger_narrow.glb")),
    ("URN",        dict(type="urn",        model="/models/urn.glb")),
    ("BIKE_RACK",  dict(type="bike_rack",  model="/models/bike_rack.glb")),
    ("TRASH_BIN",  dict(type="trash_bin",  model="/models/trash_bin.glb")),
    ("LAMP",       dict(type="lamp",       model="/models/lamp.glb")),
    ("PLAYGROUND", dict(type="playground", model="/models/playground.glb")),
    ("ENTRANCE",   dict(type="entrance",   model="/models/entrance.glb")),
    ("CROSSWALK",  dict(type="crosswalk",  model="/models/crosswalk.glb")),
    # Русские названия слоёв. Здесь проверяются все правила, а не первое
    # совпадение, поэтому важен не порядок, а отсутствие коллизий ключей.
    ("ДЕРЕВ",      dict(type="tree",       model="/models/tree.glb")),
    ("КУСТ",       dict(type="bush",       model="/models/bush.glb")),
    ("СКАМ",       dict(type="bench",      model="/models/bench.glb")),
    ("ЛАВОЧ",      dict(type="bench",      model="/models/bench.glb")),
    ("УРН",        dict(type="urn",        model="/models/urn.glb")),
    ("ФОНАР",      dict(type="lamp",       model="/models/lamp.glb")),
    ("ВЕЛОПАРКОВ", dict(type="bike_rack",  model="/models/bike_rack.glb")),
]


# Справочник видов необязателен: без него остаются общие правила выше.
SPECIES_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "plant_archetypes" / "species_catalog.json"


def _species_point_rules() -> list[tuple[str, dict]]:
    """Правило на каждый вид справочника: проекты часто называют слой
    видом («! ПР СПИРЕЯ ВАНГУТТА»), а не общим словом. Дерево или куст --
    по категории вида; лианы считаются кустарником. Без справочника -- []."""
    try:
        species = json.loads(SPECIES_CATALOG_PATH.read_text(encoding="utf-8"))
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
        # По названию вида бэкенд находит позицию каталога и 3D-модель.
        cfg["species"] = name
        rules.append((name.upper(), cfg))
    return rules


POINT_LAYER_RULES = POINT_LAYER_RULES + _species_point_rules()

# Слой с 3D-мешами зданий (только для высоты).
BUILDING_MESH_LAYER_KEYWORDS = ["BUILDING", "ЗДАНИ"]

# Фасадные элементы (3DFACE) -- геометрия для отрисовки на фасаде здания.
FACADE_LAYER_KEYWORDS = {
    "windows": ["WINDOW"],
    "canopies": ["CANOPIES", "CANOPY"],
}

# Бордюры -- линии для отрисовки на земле, а не объекты сцены.
CURB_LAYER_KEYWORDS = ["CURB", "KERB", "BORDER_STONE", "БОРТ", "БОРДЮР"]

# Единицы DXF ($INSUNITS) -> метры.
INSUNITS_TO_METERS = {0: 1.0, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 8: 0.9144}

# Пределы масштаба блока INSERT как масштаба 3D-модели: xscale задан под
# геометрию блока в чертеже и бывает 0,001, но разброс 0,7-2 -- осмысленный
# размер дерева, поэтому масштаб ограничивается, а не отбрасывается.
MIN_RENDER_SCALE = 0.3
MAX_RENDER_SCALE = 3.0


def _clamp_render_scale(xscale: float) -> float:
    return max(MIN_RENDER_SCALE, min(MAX_RENDER_SCALE, xscale))


# Допустимый прогиб при аппроксимации дуг HATCH-контура отрезками, в единицах
# DXF. План покрытий в проектах часто залит штриховкой, а не нарисован
# полилинией.
HATCH_FLATTENING_DISTANCE = 0.3


def layer_matches(layer_name: str, keywords) -> bool:
    up = layer_name.upper()
    return any(k in up for k in keywords)


def match_rule(layer_name: str, rules):
    up = layer_name.upper()
    for key, cfg in rules:
        if key in up:
            return cfg
    return None


# Все ключевые слова, по которым парсер берёт сущности со слоя. По ним разбор
# DWG-пачки заранее отбрасывает остальные слои (топосъёмка, размеры).
PARSED_LAYER_KEYWORDS = tuple(
    dict.fromkeys(
        [key for key, _ in POLYGON_RULES]
        + [key for key, _ in POINT_LAYER_RULES]
        + BOUNDARY_LAYER_KEYWORDS
        + CURB_LAYER_KEYWORDS
        + BUILDING_MESH_LAYER_KEYWORDS
        + [key for keywords in FACADE_LAYER_KEYWORDS.values() for key in keywords]
    )
)


def layer_is_parsed(layer_name: str, _cache: dict[str, bool] = {}) -> bool:  # noqa: B006 -- намеренный кеш
    """Читает ли парсер хоть что-то с этого слоя. Тексты (подписи зданий)
    парсер берёт с любого слоя -- их этот фильтр не касается."""
    if layer_name not in _cache:
        _cache[layer_name] = layer_matches(layer_name, PARSED_LAYER_KEYWORDS)
    return _cache[layer_name]
