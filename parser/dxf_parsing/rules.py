"""
Конфигурация распознавания слоёв DXF: какие подстроки в имени слоя дают
какой тип зоны ограничения (POLYGON_RULES) или точечного объекта
(POINT_LAYER_RULES), плюс ключевые слова границы, фасадов, бордюров и
единицы измерения. Сопоставление -- по подстроке без учёта регистра, в
порядке списка: побеждает первое совпадение.
"""

import json
from pathlib import Path

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
                          message="Кабель связи — слаботочная сеть, охранная зона уже, чем у силового кабеля")),
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
    # backend/core/setback_norms.py. Под "custom" они не применялись вовсе.
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
    # (_planting_zone_shapes в backend/generation/greenery_generator.py фильтрует по
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
                          message="Кабель связи — слаботочная сеть, охранная зона уже, чем у силового кабеля")),
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


# Справочник видов -- необязательный: без него (или с битым файлом) остаются
# только общие правила выше. Модульная константа, чтобы тест мог подменить путь.
SPECIES_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "plant_archetypes" / "species_catalog.json"


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
