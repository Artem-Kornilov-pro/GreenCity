"""
Нормативные отступы посадок -- единые для генератора, планировщика
(GreenPlan, ИИ-ассистент) и отчёта о нарушениях. Копия для проверки при
перетаскивании на фронтенде -- frontend/src/setbackNorms.ts; правьте оба.

Отступ отсчитывается от края зоны. У сети без подписи диаметра зона уже
включает охранный коридор (minDistance из парсера), поэтому фактическое
расстояние до оси сети всегда больше нормы. У трубы с подписью диаметра
зона -- сама труба (parser/dxf_parsing/networks.py), и отступ от её края --
ровно норма СП 42 «от наружной поверхности трубы». 0 -- не нормируется.

Таблица SETBACK_NORMS -- СП 42.13330.2016, п. 9.6, табл. 9.1 и совпадающая
с ней табл. 3.6.1 ППМ 743-ПП; значения сверяет автотест с
data/norms/sp-42-13330-2016/setbacks.csv.

Правила по породе SPECIES_SETBACK_RULES задаются по роду («Липа мелколистная»
-> «липа», ё = е) и могут только ужесточить табличный отступ: берётся
максимум из таблицы и всех подходящих правил.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Optional

PlantKind = Literal["tree", "bush"]

SETBACK_NORMS: dict[str, dict[PlantKind, float]] = {
    "building": {"tree": 5.0, "bush": 1.5},
    "road": {"tree": 2.0, "bush": 1.0},
    "gas_pipeline": {"tree": 1.5, "bush": 0.0},
    "sewer": {"tree": 1.5, "bush": 0.0},
    "water_pipeline": {"tree": 2.0, "bush": 0.0},
    # Одна строка табл. 9.1: «силовой кабель и кабель связи».
    "electrical": {"tree": 2.0, "bush": 0.7},
    "signal_cable": {"tree": 2.0, "bush": 0.7},
    # СП 42, табл. 9.1 «тепловая сеть»; 743-ПП, табл. 3.6.1 «теплопровод» --
    # «не менее 2 метров от трубопроводов» из ТЗ.
    "heat_network": {"tree": 2.0, "bush": 1.0},
    "pedestrian_path": {"tree": 0.7, "bush": 0.5},
}


@dataclass(frozen=True)
class SpeciesSetbackRule:
    """Отступ растений перечисленных родов от зоны одного типа; kinds --
    деревья, кустарники или оба (боярышник бывает и тем, и другим)."""

    genera: frozenset[str]
    zone_type: str
    distance_m: float
    kinds: frozenset[PlantKind]
    source: str


def _rule(genera: tuple[str, ...], zone_type: str, distance_m: float, kinds: tuple[PlantKind, ...], source: str):
    return SpeciesSetbackRule(frozenset(genera), zone_type, distance_m, frozenset(kinds), source)


_BOTH: tuple[PlantKind, ...] = ("tree", "bush")
EXPERT = "экспертная оценка (нормативного значения нет)"

# 743-ПП, табл. 3.6.1, прим. 3: «Деревья с широкой кроной (липа, клен, дуб,
# каштан, тополь и др.), затеняющие жилые помещения, должны сажаться не
# ближе 10 м от здания». Назначение здания в DXF не размечено -- применяется
# ко всем зданиям.
WIDE_CROWN_GENERA = ("липа", "клен", "дуб", "каштан", "тополь")
# СП 82.13330.2016, п. 9.22: колючие растения (роза, барбарис, боярышник) --
# не ближе 2 м от площадок и пешеходных коммуникаций. Шиповник -- та же роза.
THORNY_GENERA = ("роза", "шиповник", "барбарис", "боярышник")
# МГСН 1.02-02 (ППМ 623-ПП), п. 4.2.8: у теплотрасс липа, клён, сирень,
# жимолость -- не ближе 2 м; тополь, боярышник, кизильник, дерен,
# лиственница, берёза -- не ближе 3-4 м (берём 4 м).
HEAT_SENSITIVE_2M_GENERA = ("липа", "клен", "сирень", "жимолость")
HEAT_SENSITIVE_4M_GENERA = ("тополь", "боярышник", "кизильник", "дерен", "лиственница", "береза")

SPECIES_SETBACK_RULES: tuple[SpeciesSetbackRule, ...] = (
    _rule(WIDE_CROWN_GENERA, "building", 10.0, ("tree",), "ППМ 743-ПП, табл. 3.6.1, прим. 3"),
    _rule(THORNY_GENERA, "pedestrian_path", 2.0, _BOTH, "СП 82.13330.2016, п. 9.22"),
    _rule(THORNY_GENERA, "playground_zone", 2.0, _BOTH, "СП 82.13330.2016, п. 9.22"),
    _rule(HEAT_SENSITIVE_2M_GENERA, "heat_network", 2.0, _BOTH, "МГСН 1.02-02, п. 4.2.8"),
    _rule(HEAT_SENSITIVE_4M_GENERA, "heat_network", 4.0, _BOTH, "МГСН 1.02-02, п. 4.2.8"),
    # Агрессивные корни тополя и ивы у труб и фундамента: числа в нормах нет,
    # поэтому источник так и помечен.
    _rule(("тополь",), "sewer", 3.0, ("tree",), EXPERT),
    _rule(("тополь",), "water_pipeline", 3.0, ("tree",), EXPERT),
    _rule(("ива",), "building", 6.0, ("tree",), EXPERT),
    _rule(("ива",), "sewer", 3.5, ("tree",), EXPERT),
    _rule(("ива",), "water_pipeline", 3.5, ("tree",), EXPERT),
)

# Наибольший отступ -- запас для грубого отбора кандидатов по индексу.
MAX_SETBACK_M = max(
    max(v for rule in SETBACK_NORMS.values() for v in rule.values()),
    max(rule.distance_m for rule in SPECIES_SETBACK_RULES),
)

DEFAULT_TREE_SPECIES = "Липа мелколистная"


def genus_of(species: Optional[str]) -> Optional[str]:
    """Род по названию вида: первое слово, в нижнем регистре, ё -> е.
    "Клён остролистный" -> "клен", "Тополь (дерево, высокое)" -> "тополь"."""
    if not species:
        return None
    words = species.replace("(", " ").replace(",", " ").split()
    if not words:
        return None
    return words[0].lower().replace("ё", "е")


# Один вид или несколько, если на место может встать любой из них: тогда
# действует самое строгое правило.
SpeciesArg = Optional[str | Iterable[Optional[str]]]


def species_rules(species: SpeciesArg) -> frozenset[SpeciesSetbackRule]:
    """Правила по породе для этого вида (или любого из перечисленных)."""
    if species is None:
        return frozenset()
    names = [species] if isinstance(species, str) else list(species)
    genera = {genus_of(name) for name in names} - {None}
    return frozenset(rule for rule in SPECIES_SETBACK_RULES if rule.genera & genera)


# Прикорневой барьер. СП 42.13330.2016, прим. 4-7 к табл. 9.1: отступ дерева
# от подземной сети уменьшается при устройстве защитных прикорневых барьеров
# (не более чем с двух сторон ствола, из безопасного для корней материала);
# до барьера -- 0,5 м при высоте кроны до 5 м и 1 м при кроне 5-20 м. Высота
# кроны при расстановке не известна -- берётся больший минимум. Только
# деревья и только подземные сети; это мероприятие, а не норма по умолчанию:
# включается параметром GreenPlan, и посадка помечается «условно».
ROOT_BARRIER_ZONE_TYPES = frozenset(
    {"gas_pipeline", "sewer", "water_pipeline", "heat_network", "electrical", "signal_cable"}
)
ROOT_BARRIER_MIN_M = 1.0
ROOT_BARRIER_SOURCE = "СП 42.13330.2016, прим. 4–7 к табл. 9.1"
ROOT_BARRIER_MEASURE = (
    "защитный прикорневой барьер со стороны сети, не более чем с двух сторон ствола, "
    "из безопасного для корней материала"
)


# Воздушная ЛЭП. СП 42.13330.2016, прим. 2 к табл. 9.1 отсылает к ПУЭ:
# наименьшее расстояние по горизонтали от крайнего провода до кроны дерева --
# 1 м для ВЛ до 1 кВ с неизолированными проводами (ПУЭ, п. 2.4.8), 3 м до
# 20 кВ, 4 м 35-110 кВ, 5 м 150-220 кВ, 6 м 330 кВ, 7 м 500 кВ (ПУЭ,
# табл. 2.5.21). Норма -- до края кроны, а не до ствола: к ней добавляется
# радиус кроны (допущение 2,5 м -- крона 5 м, та же база, что в прим. 1 к
# табл. 9.1 СП 42). Зона ЛЭП в сцене -- коридор шириной 2 x minDistance
# вокруг провода, и отступ от её края меньше на minDistance. Класс
# напряжения: из имени слоя, параметров GreenPlan, иначе 10 кВ.
OVERHEAD_LINE = "overhead_power_line"
DEFAULT_OVERHEAD_VOLTAGE_KV = 10.0
CROWN_RADIUS_M = 2.5
_PUE_CROWN_DISTANCE_M = (
    (1.0, 1.0, "п. 2.4.8"),
    (20.0, 3.0, "табл. 2.5.21"),
    (110.0, 4.0, "табл. 2.5.21"),
    (220.0, 5.0, "табл. 2.5.21"),
    (330.0, 6.0, "табл. 2.5.21"),
    (math.inf, 7.0, "табл. 2.5.21"),
)


def pue_crown_distance(voltage_kv: Optional[float]) -> tuple[float, str]:
    """(расстояние от провода до кроны по ПУЭ, м; пункт) для класса напряжения."""
    kv = DEFAULT_OVERHEAD_VOLTAGE_KV if voltage_kv is None else voltage_kv
    return next((d, clause) for limit, d, clause in _PUE_CROWN_DISTANCE_M if kv <= limit)


def overhead_line_setback(zone_min_distance: float, voltage_kv: Optional[float]) -> float:
    """Отступ ствола дерева от края зоны ЛЭП (коридора вокруг провода)."""
    crown, _clause = pue_crown_distance(voltage_kv)
    return round(max(0.0, crown + CROWN_RADIUS_M - zone_min_distance), 3)


def overhead_line_source(voltage_kv: Optional[float]) -> str:
    _distance, clause = pue_crown_distance(voltage_kv)
    kv = "напряжение не указано — принято 10 кВ" if voltage_kv is None else f"{voltage_kv:g} кВ"
    return f"ПУЭ, {clause} ({kv}): до кроны, крона принята 5 м; СП 42.13330.2016, прим. 2 к табл. 9.1"


def root_barrier_applies(zone_type: str, plant_kind: PlantKind) -> bool:
    return plant_kind == "tree" and zone_type in ROOT_BARRIER_ZONE_TYPES


def setback_for(
    zone_type: str,
    plant_kind: PlantKind,
    zone_min_distance: float,
    species: SpeciesArg = None,
    root_barrier: bool = False,
    voltage_kv: Optional[float] = None,
) -> float:
    """Требуемый отступ посадки от зоны: табличная норма (для типа зоны вне
    таблицы -- minDistance самой зоны), ужесточённая правилами по породе.
    root_barrier -- дерево с прикорневым барьером: от подземной сети хватает
    ROOT_BARRIER_MIN_M (правила по породе по-прежнему могут ужесточить).
    voltage_kv -- класс напряжения ЛЭП: дерево от ЛЭП -- по ПУЭ."""
    rule = SETBACK_NORMS.get(zone_type)
    distance = rule[plant_kind] if rule else zone_min_distance
    if zone_type == OVERHEAD_LINE and plant_kind == "tree":
        distance = overhead_line_setback(zone_min_distance, voltage_kv)
    if root_barrier and root_barrier_applies(zone_type, plant_kind):
        distance = min(distance, ROOT_BARRIER_MIN_M)
    for species_rule in species_rules(species):
        if species_rule.zone_type == zone_type and plant_kind in species_rule.kinds:
            distance = max(distance, species_rule.distance_m)
    return distance


TABLE_SOURCE = "СП 42.13330.2016, п. 9.6, табл. 9.1; ППМ 743-ПП, табл. 3.6.1"


def setback_basis(
    zone_type: str,
    plant_kind: PlantKind,
    zone_min_distance: float,
    species: SpeciesArg = None,
    root_barrier: bool = False,
    voltage_kv: Optional[float] = None,
) -> tuple[float, str]:
    """(отступ, акт и пункт) -- то же число, что setback_for(), и документ,
    который его задаёт."""
    rule = SETBACK_NORMS.get(zone_type)
    if rule:
        distance, source = rule[plant_kind], TABLE_SOURCE
    elif zone_type == OVERHEAD_LINE and plant_kind == "tree":
        distance, source = overhead_line_setback(zone_min_distance, voltage_kv), overhead_line_source(voltage_kv)
    else:
        distance, source = zone_min_distance, "охранная зона по чертежу (отступ зоны)"
    if root_barrier and root_barrier_applies(zone_type, plant_kind) and distance > ROOT_BARRIER_MIN_M:
        distance, source = ROOT_BARRIER_MIN_M, ROOT_BARRIER_SOURCE
    for species_rule in species_rules(species):
        if species_rule.zone_type == zone_type and plant_kind in species_rule.kinds and species_rule.distance_m > distance:
            distance, source = species_rule.distance_m, species_rule.source
    return distance, source


def plant_kind_of_object_type(object_type: str) -> Optional[PlantKind]:
    if object_type in ("tree", "bush"):
        return object_type  # type: ignore[return-value]
    return None
