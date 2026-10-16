"""
Распознавание слоёв, названных как угодно.

Заказчики передают чертежи как им удобно, единых имён слоёв нет: «Газ_НД»,
«Тепловые сети», «СТРОЕНИЯ», «Подоснова|Здания», «ExistingTrees», латинская
«С» в русском слове, вся подоснова одним блоком на слое «0». Правила rules.py
знают ключевые слова; здесь имя слоя (или сущность) сводится к ключу, который
эти правила узнают. Сами правила и их порядок не меняются, поэтому слой,
который распознавался раньше, распознаётся так же.

Порядок для слоя, срабатывает первое:

1. Назначение пользователя (layer_map): «этот слой -- здания».
2. Имя как есть -- прежнее поведение, кроме известных ложных совпадений
   («STREET» содержит «TREE», «ДЕРЕВЯННЫЙ» -- «ДЕРЕВ»).
3. Нормализованное имя: без префикса внешней ссылки, ё -> е, латиница в
   русском слове -> кириллица и наоборот, CamelCase разделён на слова.
4. Синонимы по словам имени: «Тепловые сети», «Проезды», «Газ», «Т1»,
   «КЛ 10кВ», «Липы».

Сущность со слоя, который по имени не дал её класса, распознаётся по
содержимому: вставка блока -- по имени блока, штриховка -- по образцу, линия --
по типу линии (сети в СПДС часто рисуют типом линии с буквой «В1», «К1»).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Optional

from dxf_parsing.encoding import repair_mojibake
from dxf_parsing.rules import (
    BOUNDARY_LAYER_KEYWORDS,
    BUILDING_MESH_LAYER_KEYWORDS,
    CURB_LAYER_KEYWORDS,
    FACADE_LAYER_KEYWORDS,
    POINT_LAYER_EXCLUDE_KEYWORDS,
    POINT_LAYER_RULES,
    POLYGON_RULES,
    SKIP_LAYER_KEYWORDS,
    SPECIES_CATALOG_PATH,
    layer_matches,
    match_rule,
)

# ---------------------------------------------------------------------------
# Классы слоёв: что пользователь может назначить слою и что показывается в
# сводке распознавания. Ключ -- имя слоя, которое правила rules.py узнают
# ровно как этот класс (проверяет тест).

IGNORE_KEY = "__IGNORED__"

LAYER_KINDS: dict[str, tuple[str, str]] = {
    "boundary": ("BOUNDARY", "Граница участка"),
    "building": ("BUILDING", "Здания и сооружения"),
    "transformer": ("TRANSFORMER", "Трансформаторная подстанция"),
    "road": ("ROAD", "Проезжая часть, проезды"),
    "parking": ("PARKING", "Парковка"),
    "path": ("PATHS", "Тротуары и дорожки"),
    "playground": ("PLAYGROUND", "Площадки"),
    "lawn": ("GRASS", "Газон"),
    "protected": ("PROTECT", "Охраняемая зона (посадка запрещена)"),
    "gas": ("GAS", "Газопровод"),
    "water": ("WATER_PIPELINE", "Водопровод"),
    "sewer": ("SEWER", "Канализация, водосток, дренаж"),
    "heat": ("HEAT", "Теплосеть"),
    "electrical": ("ELECTR_CABLE", "Электрокабель"),
    "signal_cable": ("CABLE_COMM", "Кабель связи"),
    "overhead_power_line": ("OVERHEAD_POWER_LINE", "Воздушная ЛЭП"),
    "underground": ("ПОДЗЕМН", "Подземные сети (вид не указан)"),
    "tree": ("TREE", "Деревья"),
    "bush": ("BUSH", "Кустарники"),
    "lamp": ("LAMP", "Фонари"),
    "bench": ("BENCH", "Скамейки"),
    "urn": ("URN", "Урны"),
    "curb": ("CURB", "Бордюры"),
    "skip": ("LABEL", "Оформление: подписи, размеры (не учитывается)"),
    "ignore": (IGNORE_KEY, "Не учитывать"),
}

# Ключи, которые дают синонимы помимо классов выше.
_LAMP_CABLE = "LAMP_CABLE"  # наружное освещение: опоры -- фонари, линии -- кабель
_TRASH = "TRASH_BIN"
_BIKE = "BIKE_RACK"
_ENTRANCE = "ENTRANCE"
_LOUNGER = "LOUNGER"
_CROSSWALK = "CROSSWALK"

# Граница участка, найденная по синонимам («Граница участка», «Граница ЗУ»,
# просто «Граница»), -- в отдельном ярусе: она берётся, только если слоя
# границы с прежними именами нет, иначе две границы склеились бы в одну.
BOUNDARY_TIER_KEYS = ("__GR_TIER1__", "__GR_TIER2__")

# ---------------------------------------------------------------------------
# Нормализация имени.

# Внешняя ссылка: «Подоснова|Здания», привязанная -- «Подоснова$0$Здания».
_XREF_BOUND = re.compile(r"\$\d+\$")
_CAMEL = re.compile(r"(?<=[a-zа-яё])(?=[A-ZА-ЯЁ])")
_TOKEN_SPLIT = re.compile(r"[^0-9A-ZА-Я]+")
_LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
_CYR_TO_LAT = str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY")
_CYR = re.compile(r"[А-Я]")
_LAT = re.compile(r"[A-Z]")


def _strip_xref(name: str) -> str:
    if "|" in name:
        name = name.rsplit("|", 1)[1]
    return _XREF_BOUND.split(name)[-1]


# Буквы, у которых нет двойника в другом алфавите: по ним видно, на каком
# языке слово, даже если двойников в нём больше («БEPEЗA»).
_ONLY_CYR = re.compile(r"[БГДЖЗИЙЛПФЦЧШЩЪЫЬЭЮЯ]")
_ONLY_LAT = re.compile(r"[DFGIJLNQRSUVWZ]")


def _fix_mixed_script(token: str) -> str:
    """«ГAЗОН» с латинской A -> «ГАЗОН»; «TРEE» с кириллицей -> «TREE»."""
    cyr, lat = len(_CYR.findall(token)), len(_LAT.findall(token))
    if not cyr or not lat:
        return token
    if _ONLY_CYR.search(token):
        return token.translate(_LATIN_TO_CYR)
    if _ONLY_LAT.search(token):
        return token.translate(_CYR_TO_LAT)
    return token.translate(_LATIN_TO_CYR) if cyr >= lat else token.translate(_CYR_TO_LAT)


def name_tokens(name: str) -> list[str]:
    """Слова имени слоя в верхнем регистре, без префикса внешней ссылки и без
    кракозябр (encoding.repair_mojibake)."""
    name = _CAMEL.sub(" ", _strip_xref(repair_mojibake(name)))
    upper = name.upper().replace("Ё", "Е")
    return [_fix_mixed_script(t) for t in _TOKEN_SPLIT.split(upper) if t]


# ---------------------------------------------------------------------------
# Ложные совпадения прежних правил: слово целиком содержит ключевое слово, но
# значит другое. Такое слово закрывается, и имя распознаётся дальше по
# остальным словам и синонимам.
_FALSE_MATCH_WORDS = (
    "STREET",  # TREE
    "TURN",  # URN: RETURN, SATURN, TURNING
    "CLAMP",  # LAMP
    "SPARK",  # PARK
    "PARKWAY",  # PARK -- это дорога
    "VEGAS",  # GAS
    "WHEAT",  # HEAT
    "THEAT",  # HEAT: THEATER, THEATRE
    "WATERPROOF",  # WATER
    "STORMWATER",  # WATER -- ливнёвка, это канализация
    "WASTEWATER",
    "RAINWATER",
    "COMPOSITE",  # SITE
    "WEBSITE",
    "TEXTURE",  # TEXT
    "ДЕРЕВЯН",  # ДЕРЕВ: деревянный настил -- не деревья
    "ЖУРНАЛ",  # УРН
)
_MASK = "·"

# Общие слова границы участка: слой «SITE_TREES» или «ROAD_BOUNDARY» -- не
# граница, если остальные слова имени дают другой класс.
_GENERIC_BOUNDARY_WORDS = ("BOUNDARY", "TERRITORY", "SITE")


def _mask_words(upper: str, words) -> str:
    for word in words:
        if word in upper:
            upper = upper.replace(word, _MASK * len(word))
    return upper


def _matches_object_or_zone(upper: str) -> bool:
    return match_rule(upper, POLYGON_RULES) is not None or _point_rule(upper) is not None


# «Телефонная/кабельная канализация» -- трубы для кабелей, а не канализация.
_CABLE_DUCT = re.compile(r"(?<=ТЕЛЕФОН|КАБЕЛЬН)(\w*[\s_.-]+)(КАНАЛИЗАЦ)")


def _mask_false_matches(upper: str) -> str:
    upper = _mask_words(upper, _FALSE_MATCH_WORDS)
    upper = _CABLE_DUCT.sub(lambda m: m.group(1) + _MASK * len(m.group(2)), upper)
    if layer_matches(upper, _GENERIC_BOUNDARY_WORDS) and not layer_matches(upper, _SPECIFIC_BOUNDARY):
        rest = _mask_words(upper, _GENERIC_BOUNDARY_WORDS)
        if _matches_object_or_zone(rest):
            upper = rest
    return upper


_SPECIFIC_BOUNDARY = [k for k in BOUNDARY_LAYER_KEYWORDS if k not in _GENERIC_BOUNDARY_WORDS]

_ALL_KEYWORDS = tuple(
    dict.fromkeys(
        [key for key, _ in POLYGON_RULES]
        + [key for key, _ in POINT_LAYER_RULES]
        + list(BOUNDARY_LAYER_KEYWORDS)
        + list(SKIP_LAYER_KEYWORDS)
        + list(CURB_LAYER_KEYWORDS)
        + list(BUILDING_MESH_LAYER_KEYWORDS)
        + [key for keywords in FACADE_LAYER_KEYWORDS.values() for key in keywords]
    )
)

_point_rule_cache: dict[str, Optional[dict]] = {}


def _point_rule(key: str) -> Optional[dict]:
    """Правило точечного объекта для ключа -- первое из POINT_LAYER_RULES."""
    if key not in _point_rule_cache:
        up = key.upper()
        rule = None
        if not layer_matches(up, POINT_LAYER_EXCLUDE_KEYWORDS):
            rule = next((cfg for kw, cfg in POINT_LAYER_RULES if kw in up), None)
        _point_rule_cache[key] = rule
    return _point_rule_cache[key]


@lru_cache(maxsize=65536)
def is_known(key: Optional[str]) -> bool:
    """Узнают ли правила rules.py этот ключ хоть как-то."""
    return bool(key) and layer_matches(key, _ALL_KEYWORDS)


# ---------------------------------------------------------------------------
# Синонимы по словам имени. Условие -- корень (слово начинается с него),
# «=СЛОВО» -- слово целиком, «~выражение» -- слово целиком по регулярному
# выражению, кортеж -- все условия сразу на разных словах. Порядок значим:
# узкое раньше общего, сети раньше площадей, площади раньше точек.

_SYNONYMS: list[tuple[str, tuple]] = [
    # Тепловой пункт -- здание, а не трасса.
    ("BUILDING", (("ТЕПЛОВ", "ПУНКТ"),)),
    ("LABEL", ("ПОДПИС", "НАДПИС", "ТЕКСТ", "РАЗМЕР", "РАЗМЕТК", "ВЫНОСК", "ОТМЕТК", "ГОРИЗОНТАЛ", "ШТАМП", "ANNO", "=NOTE", "=NOTES", "TITLE", "CONTOUR")),
    # Сети: связь раньше кабеля, освещение раньше электросетей.
    ("CABLE_COMM", (
        "СВЯЗ", "ТЕЛЕФОН", "=ТЛФ", "СЛАБОТОЧ", "=ВОЛС", "ОПТОВОЛ", "ОПТИЧ", "ИНТЕРНЕТ", "=СС", "=НСС", "РАДИОФИК",
        "=МГТС", "ТЕЛЕКОМ", "TELECOM", "TELEPHONE", "=TEL", "FIBER", "FIBRE", "COMMUNICATION", "=COMM", "=COMMS",
    )),
    ("OVERHEAD_POWER_LINE", ("~ВЛ\\d*", "ВОЗДУШН", "AERIAL")),
    (_LAMP_CABLE, ("ОСВЕЩ", "LIGHTING", "=LIGHT", "=LIGHTS")),
    ("ELECTR_CABLE", (
        "~ЭЛ\\d*", "=ЭС", "~КЛ\\d*", "ЭНЕРГОСН", "СИЛОВ", "~\\d+КВ", "~\\d+KV", "ELECTRIC",
    )),
    ("HEAT", (
        "ТЕПЛОВ", "ТЕПЛОСН", "ТЕПЛОМАГ", "=ТЕПЛО", "~ТС\\d*", "=ГВС", "~Т\\d{1,2}", "~T\\d{1,2}", "STEAM",
    )),
    ("GAS", ("=ГАЗ", "ГАЗОСН", "ГАЗОВ", "ГАЗОРАСП", "ГАЗОП", "~Г\\d{1,2}", "~Г[НСВ]Д")),
    ("SEWER", (
        "КАНАЛИЗ", "ЛИВНЕВ", "ДОЖДЕВ", "ВОДООТВОД", "ВОДООТВЕД", "ДРЕН", "ФЕКАЛ", "~К\\d{1,2}", "~K\\d{1,2}",
        "SANITARY", "STORM", "DRAIN", "WASTEWATER", "RAINWATER", "FOUL", "CULVERT",
    )),
    ("WATER_PIPELINE", ("ВОДОПР", "ВОДОСНАБ", "ВОДОВОД", "=ХВС", "=ВОДА", "~В\\d{1,2}", "~B\\d{1,2}")),
    ("ПОДЗЕМН", ("КОММУНИКАЦ", ("ИНЖЕНЕРН", "СЕТ"), "UTILIT", "UNDERGROUND")),
    ("TRANSFORMER", ("ТРАНСФОРМ", "ПОДСТАНЦ", "~\\d?Б?[КР]?ТП", "SUBSTATION")),
    # Площади.
    ("BUILDING", (
        "ЗДАН", "СТРОЕН", "СООРУЖ", "=ДОМ", "=ДОМА", "КОРПУС", "ГАРАЖ", "КИОСК", "ПАВИЛЬОН",
        "=ЦТП", "=ИТП", "=ОКС", "BLDG", "BUILD", "HOUSE", "FOOTPRINT",
    )),
    ("PARKING", ("ПАРКОВК", "ПАРКОВОЧ", "ПАРКИНГ", "СТОЯНК", "АВТОСТОЯН", "МАШИНОМЕСТ")),
    ("PLAYGROUND", ("ДЕТСК", "ИГРОВ", "ПЛОЩАДК", "ХОЗПЛОЩАД", "СПОРТ", "ВОРКАУТ", "ФИТНЕС", "ТРЕНАЖ", "PLAY", "SPORT", "=COURT", "=COURTS")),
    # Пешеходный переход -- точка перехода, а не дорожка.
    (_CROSSWALK, (("ПЕШЕХОДН", "ПЕРЕХОД"), "=ЗЕБРА")),
    # Тропиночная сеть -- раньше дорог: «дорожно-тропиночная» -- не проезд.
    ("PATHS", (
        "ТРОПИН", "ДОРОЖК", "ПЕШЕХОД", "ТРОТУАР", "АЛЛЕ", "ПОКРЫТ", "ПЛИТК", "МОЩЕН", "БРУСЧАТ", "ВЕЛОДОР", "ОТМОСТК",
        "КРЫЛЬЦ", "=ПЛОЩАДЬ", "FOOTWAY", "PAVING", "PAVER", "PAVEMENT", "TRAIL", "PROMENADE", "=PLAZA", "BOARDWALK",
        "CYCLEWAY", "BIKEWAY",
    )),
    ("ROAD", (
        "ДОРОГ", "ДОРОЖН", "ПРОЕЗД", "ПРОЕЗЖ", "АВТОДОР", "МАГИСТРАЛ", "ШОССЕ", "АСФАЛЬТ", "=ДОР", "=УДС",
        "STREET", "CARRIAGEWAY", "DRIVEWAY", "=DRIVE", "PARKWAY", "HIGHWAY", "ASPHALT", "=RD",
    )),
    ("GRASS", ("ТРАВ", "ОЗЕЛЕН", "=ЗНОП", ("ЗЕЛЕН", "НАСАЖД"), "TURF", "=GREEN", "GREENERY", "VEGETATION")),
    # Точечные объекты.
    ("TREE", ("ХВОЙН", "~ДЕР\\d*", "ЛИСТВЕН", "=TREES", "CONIFER", "DECIDUOUS")),
    ("BUSH", ("ИЗГОРОД", "HEDGE", "=BUSHES")),
    ("LAMP", ("СВЕТИЛЬН", "=ОПОРА", "=ОПОРЫ", "LUMINAIRE", "STREETLIGHT", "=POLE", "=POLES", "LIGHTPOLE")),
    ("BENCH", ("ЛАВК", "ДИВАН", "=СКАМЬИ", "SEAT")),
    (_TRASH, ("МУСОР", "=ТКО", "TRASH", "LITTER", "=BIN", "=BINS")),
    (_BIKE, ("ВЕЛОСТОЯН",)),
    (_LOUNGER, ("ШЕЗЛОНГ", "LOUNGER")),
    (_ENTRANCE, ("=ПОДЪЕЗД", "=ПОДЪЕЗДЫ", "=ВХОД", "=ВХОДЫ", "ВХОДН", "=ENTRY", "=ENTRIES", "DOOR")),
]

# Граница участка по синонимам. Ярус 1 -- «граница» с уточнением, что это
# участок; ярус 2 -- просто «Граница». Слова исключений («Граница улицы»,
# «Граница площадки», «Граница охранной зоны») -- не граница участка.
_BOUNDARY_ROOT = "ГРАНИЦ"
_BOUNDARY_TIER1 = ("УЧАСТК", "=ЗУ", "ТЕРРИТОР", "ОТВОД", "ПРОЕКТ", "БЛАГОУСТР", "РАБОТ", "ОБЪЕКТ", "ЗЕМЕЛЬН", "КАДАСТР", "ДВОР")
_BOUNDARY_EXCLUDE = ("УЛИЦ", "ПЛОЩАДК", "ОХРАН", "ЗОН", "САНИТАР", "ВОДООХР", "ЗАСТРОЙ", "КРАСН", "ГАЗОН")
_BOUNDARY_ENGLISH = ("PLOT", "PARCEL", "=PROPERTY", "LIMIT")


def _cond_matches(cond, tokens: list[str]) -> bool:
    if isinstance(cond, tuple):
        return all(_cond_matches(c, tokens) for c in cond)
    if cond.startswith("="):
        return cond[1:] in tokens
    if cond.startswith("~"):
        return any(re.fullmatch(cond[1:], t) for t in tokens)
    return any(t.startswith(cond) for t in tokens)


def _genus_forms() -> dict[str, str]:
    """Род из справочника видов -> «TREE»/«BUSH», во всех ходовых формах
    слова: «ЛИПА», «ЛИПЫ»; «ЕЛЬ», «ЕЛИ»; «КЛЕН», «КЛЕНЫ». Род, который в
    справочнике и дерево, и кустарник, -- по большинству видов."""
    try:
        species = json.loads(SPECIES_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        species = []
    votes: dict[str, list[int]] = {}
    for entry in species:
        words = (entry.get("name") or "").replace("(", " ").replace(",", " ").split()
        if not words:
            continue
        genus = words[0].upper().replace("Ё", "Е")
        if len(genus) < 3 or genus.endswith(("ИЙ", "ЫЙ")):
            continue  # «Девичий виноград», «Кедровый стланик» -- не род
        tree = "дерев" in (entry.get("category") or "").lower()
        votes.setdefault(genus, [0, 0])[0 if tree else 1] += 1
    for genus in ("КАШТАН", "ОСИНА", "ОЛЬХА", "ГРАБ", "КЕДР"):
        votes.setdefault(genus, [1, 0])
    forms = {}
    for genus, (trees, bushes) in votes.items():
        key = "TREE" if trees >= bushes else "BUSH"
        if genus.endswith("Я"):
            words = {genus, genus[:-1] + "И"}
        elif genus.endswith("А"):
            words = {genus, genus[:-1] + "Ы", genus[:-1] + "И"}
        elif genus.endswith("Ь"):
            words = {genus, genus[:-1] + "И"}
        else:
            words = {genus, genus + "Ы", genus + "И", genus + "А"}
        for word in words:
            forms.setdefault(word, key)
    return forms


_GENUS_FORMS = _genus_forms()


# Имя из одного слова, которое в составе других имён значит иное. Буква сети
# без номера («Г», «К»): в имени из нескольких слов это обычно предлог
# («Дорожка к дому»). «Сети»: «дорожно-тропиночная сеть» -- не коммуникации.
_SINGLE_LETTER_CODES = {"Г": "GAS", "К": "SEWER", "В": "WATER_PIPELINE", "Т": "HEAT", "СЕТИ": "ПОДЗЕМН", "СЕТЬ": "ПОДЗЕМН"}


def _synonym_key(tokens: list[str]) -> Optional[str]:
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0] in _SINGLE_LETTER_CODES:
        return _SINGLE_LETTER_CODES[tokens[0]]
    if any(t.startswith(_BOUNDARY_ROOT) for t in tokens) and not any(
        _cond_matches(c, tokens) for c in _BOUNDARY_EXCLUDE
    ):
        if any(_cond_matches(c, tokens) for c in _BOUNDARY_TIER1):
            return BOUNDARY_TIER_KEYS[0]
        if all(t.startswith(_BOUNDARY_ROOT) for t in tokens):
            return BOUNDARY_TIER_KEYS[1]
    for key, conditions in _SYNONYMS:
        if any(_cond_matches(c, tokens) for c in conditions):
            return key
    for token in tokens:
        if token in _GENUS_FORMS:
            return _GENUS_FORMS[token]
    if any(_cond_matches(c, tokens) for c in _BOUNDARY_ENGLISH):
        return BOUNDARY_TIER_KEYS[0]
    return None


# ---------------------------------------------------------------------------
# Содержимое сущности.

# Стандартные типы линий AutoCAD и nanoCAD ничего не говорят о сети.
_PLAIN_LINETYPES = re.compile(
    r"^(BYLAYER|BYBLOCK|CONTINUOUS|DASHED\d*X?|HIDDEN\d*X?|CENTER\d*X?|PHANTOM\d*X?|DOT\d*X?|DASHDOT\d*X?|"
    r"DIVIDE\d*X?|BORDER\d*X?|ACAD_ISO.*|ISO.*|FENCELINE\d*|TRACKS|BATTING|ZIGZAG|СПЛОШНАЯ|ШТРИХОВАЯ.*|"
    r"ШТРИХПУНКТИРН.*|ОСЕВАЯ.*|ПУНКТИР.*|ТОНКАЯ.*|ОСНОВНАЯ.*)$"
)
_LINETYPE_KEYS = {"GAS_LINE": "GAS", "HOT_WATER_SUPPLY": "HEAT"}
_NETWORK_KEYS = {"GAS", "SEWER", "WATER_PIPELINE", "HEAT", "ELECTR_CABLE", "CABLE_COMM", "OVERHEAD_POWER_LINE", "ПОДЗЕМН", _LAMP_CABLE}
_PAVING_PATTERNS = re.compile(r"^(AR-B816.*|AR-B88|AR-BRELM|AR-BRSTD|AR-HBONE|AR-PARQ1|GRAVEL|BRICK|BRSTONE)$")
_LINE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE", "ELLIPSE")


class LayerResolver:
    """Ключ для правил rules.py по имени слоя и по сущности.

    layer_map -- назначения пользователя {имя слоя: класс из LAYER_KINDS}."""

    def __init__(self, layer_map: Optional[Mapping[str, str]] = None):
        self.layer_map = {name: kind for name, kind in (layer_map or {}).items() if kind in LAYER_KINDS}
        self._layers: dict[str, tuple[Optional[str], Optional[str]]] = {}
        self._names: dict[str, Optional[str]] = {}
        self._linetypes: dict[str, Optional[str]] = {}
        self._layer_linetypes: dict[str, str] = {}

    # --- слой ---------------------------------------------------------------

    def resolve_layer(self, name: str) -> tuple[Optional[str], Optional[str]]:
        """(ключ, как распознан) или (None, None). Как распознан: override,
        name, normalized, synonym."""
        if name not in self._layers:
            self._layers[name] = self._resolve_layer(name)
        return self._layers[name]

    def _resolve_layer(self, name: str) -> tuple[Optional[str], Optional[str]]:
        kind = self.layer_map.get(name)
        if kind:
            return LAYER_KINDS[kind][0], "override"
        masked = _mask_false_matches(name.upper())
        if is_known(masked):
            return masked, "name"
        key = _name_key(name)
        if key is not None:
            return key
        return None, None

    def layer_key(self, name: str) -> str:
        """Ключ слоя для сопоставления с правилами; нераспознанный -- само имя
        (правила его и так не узнают)."""
        key, _ = self.resolve_layer(name)
        return key if key is not None else name

    # --- сущность -----------------------------------------------------------

    def key(self, entity) -> str:
        """Ключ сущности: по слою, а если слой не дал ей класса -- по
        содержимому (имя блока, образец штриховки, тип линии)."""
        layer = entity.dxf.layer
        key, source = self.resolve_layer(layer)
        if source == "override":
            return key
        kind = entity.dxftype()
        if kind == "INSERT":
            if key is not None and _point_rule(key) is not None:
                return key
            block_key = self.block_key(entity.dxf.name)
            if block_key is not None and _point_rule(block_key) is not None:
                return block_key
        elif key is not None and key not in ("ПОДЗЕМН",):
            return key
        elif kind == "HATCH":
            pattern_key = self._pattern_key(entity)
            if pattern_key is not None:
                return pattern_key
        elif kind in _LINE_TYPES:
            linetype_key = self._linetype_key(entity)
            if linetype_key is not None:
                return linetype_key
        return key if key is not None else layer

    def block_key(self, block_name: str) -> Optional[str]:
        """Ключ по имени блока: «Липа_мелколистная», «Дерево_хвойное», «Фонарь».
        Анонимные блоки (*U12) ничего не говорят."""
        if not block_name or block_name.startswith("*"):
            return None
        return self._cached_name_key(block_name)

    def _cached_name_key(self, name: str) -> Optional[str]:
        if name not in self._names:
            masked = _mask_false_matches(name.upper())
            if is_known(masked):
                self._names[name] = masked
            else:
                found = _name_key(name)
                self._names[name] = found[0] if found else None
        return self._names[name]

    def _pattern_key(self, hatch) -> Optional[str]:
        pattern = (hatch.dxf.get("pattern_name") or "").upper()
        if not pattern or pattern == "SOLID":
            return None
        if _PAVING_PATTERNS.match(pattern):
            return "PATHS"
        key = self._cached_name_key(pattern)
        return key if key == "GRASS" or (key and "GRASS" in key) or (key and "ГАЗОН" in key) else None

    def _linetype_key(self, entity) -> Optional[str]:
        linetype = entity.dxf.get("linetype") or "BYLAYER"
        if linetype.upper() in ("BYLAYER", "BYBLOCK"):
            layer = entity.dxf.layer
            if layer not in self._layer_linetypes:
                self._layer_linetypes[layer] = _layer_linetype(entity) or ""
            linetype = self._layer_linetypes[layer]
        if linetype not in self._linetypes:
            self._linetypes[linetype] = self._linetype_name_key(linetype)
        return self._linetypes[linetype]

    def _linetype_name_key(self, linetype: str) -> Optional[str]:
        upper = linetype.upper().replace("Ё", "Е")
        if not upper or _PLAIN_LINETYPES.match(upper):
            return None
        if upper in _LINETYPE_KEYS:
            return _LINETYPE_KEYS[upper]
        key = self._cached_name_key(linetype)
        if key is None:
            return None
        if key in _NETWORK_KEYS:
            return key
        # Ключ из прежних правил («ГАЗОПРОВОД», «WATER») -- только если это сеть.
        cfg = match_rule(key, POLYGON_RULES)
        if cfg and cfg["type"] in ("gas_pipeline", "sewer", "water_pipeline", "heat_network", "electrical", "signal_cable", "overhead_power_line"):
            return key
        return None


def _name_key(name: str) -> Optional[tuple[str, str]]:
    """Шаги 3-4: нормализованное имя, затем синонимы. Нормализованное имя --
    в двух написаниях сразу, через пробел и через подчёркивание: среди
    ключевых слов есть и «СПИРЕЯ ВАНГУТТА», и «CABLE_COMM»."""
    tokens = name_tokens(name)
    if not tokens:
        return None
    masked = _mask_false_matches(" ".join(tokens) + " / " + "_".join(tokens))
    if is_known(masked):
        return masked, "normalized"
    key = _synonym_key(tokens)
    if key is not None:
        return key, "synonym"
    return None


def _layer_linetype(entity) -> Optional[str]:
    doc = entity.doc
    if doc is None:
        return None
    try:
        return doc.layers.get(entity.dxf.layer).dxf.linetype
    except Exception:  # noqa: BLE001 -- слоя нет в таблице: тип линии неизвестен
        return None


def layer_key_of(source, entity) -> str:
    """Ключ сущности для правил: у вида чертежа (DrawingView) -- с учётом
    синонимов и содержимого, у голого modelspace -- имя слоя, как раньше."""
    resolver = getattr(source, "resolver", None)
    return resolver.key(entity) if resolver is not None else entity.dxf.layer


# ---------------------------------------------------------------------------
# Сводка распознавания для пользователя.

_ZONE_KIND = {
    "building": "building",
    "transformer": "transformer",
    "road": "road",
    "playground_zone": "playground",
    "gas_pipeline": "gas",
    "sewer": "sewer",
    "water_pipeline": "water",
    "signal_cable": "signal_cable",
    "electrical": "electrical",
    "overhead_power_line": "overhead_power_line",
    "heat_network": "heat",
    "pedestrian_path": "path",
}
_OBJECT_KIND = {"tree": "tree", "bush": "bush", "lamp": "lamp", "bench": "bench", "urn": "urn", "trash_bin": "urn"}
_OBJECT_LABEL = {
    "lounger": "Шезлонги",
    "bike_rack": "Велопарковки",
    "playground": "Игровое оборудование",
    "entrance": "Входы в здания",
    "crosswalk": "Пешеходные переходы",
}


@lru_cache(maxsize=65536)
def describe_key(key: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(класс из LAYER_KINDS или None, подпись по-русски) для ключа слоя --
    что из этого слоя возьмёт разбор."""
    if not key or key == IGNORE_KEY:
        return ("ignore", LAYER_KINDS["ignore"][1]) if key == IGNORE_KEY else (None, None)
    if key in BOUNDARY_TIER_KEYS or (layer_matches(key, BOUNDARY_LAYER_KEYWORDS)):
        return "boundary", LAYER_KINDS["boundary"][1]
    if layer_matches(key, SKIP_LAYER_KEYWORDS):
        return "skip", LAYER_KINDS["skip"][1]
    zone = match_rule(key, POLYGON_RULES)
    point = _point_rule(key)
    if zone is not None:
        kind = _ZONE_KIND.get(zone["type"])
        if zone["type"] == "custom":
            kind = "parking" if "парков" in zone["message"].lower() else "underground"
        elif zone["type"] == "protected_zone":
            if zone["severity"] != "allowed":
                kind = "protected"
            elif zone["message"].startswith("Открытая земля"):
                return "ground", "Открытая земля (место под посадку)"
            else:
                kind = "lawn"
        if kind == "electrical" and point is not None and point["type"] == "lamp":
            return "lamp", "Наружное освещение: фонари и кабель"
        return kind, LAYER_KINDS[kind][1] if kind else zone["message"]
    if point is not None:
        kind = _OBJECT_KIND.get(point["type"])
        label = LAYER_KINDS[kind][1] if kind else _OBJECT_LABEL.get(point["type"], point["type"])
        if point.get("species"):
            label = f"{label}: {point['species']}"
        return kind, label
    if layer_matches(key, CURB_LAYER_KEYWORDS):
        return "curb", LAYER_KINDS["curb"][1]
    if layer_matches(key, BUILDING_MESH_LAYER_KEYWORDS):
        return "building", LAYER_KINDS["building"][1]
    if any(layer_matches(key, kws) for kws in FACADE_LAYER_KEYWORDS.values()):
        return None, "Фасады (окна, козырьки)"
    return None, None
