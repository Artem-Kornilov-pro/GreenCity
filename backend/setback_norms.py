"""
Единый источник правды для нормативных отступов посадок -- используется
всеми, кто ставит или проверяет посадки: генератор по сетке
(greenery_generator.py), планировщик (placement.Placer -- правка текстом,
design_area, GreenPlan), отчёт о нарушениях (violation_report.py).
Фронтенд держит копию в frontend/src/setbackNorms.ts для проверки при ручном
drag&drop (frontend/src/geometry.ts::checkViolations). Синхронизация между
.ts и .py ручная (нет общей кодогенерации схемы, см. backend/schemas.py) --
при правке таблиц обнови оба файла.

Это ДОПОЛНИТЕЛЬНЫЙ запас поверх уже нарисованной зоны (для труб/кабелей она
уже отбуферена на minDistance из parser/parse_dxf.py -- это охранная зона
самой сети, не связанная с видом посадки). 0 означает "не нормируется".
Нормы в первоисточниках даны от оси/стенки сети, а мы отсчитываем от края
уже расширенного коридора -- то есть всегда строже нормы на полуширину
коридора, не мягче (подробнее data/norms/sp-42-13330-2016/README.md).

--- Общая таблица по типу зоны (SETBACK_NORMS) ---

СП 42.13330.2016, п. 9.6, таблица 9.1, и совпадающая с ней таблица 3.6.1
ППМ 743-ПП. Значения сверяются с data/norms/sp-42-13330-2016/setbacks.csv
автотестом (tests/test_setback_norms.py), а не только глазами.

--- Правила по породе (SPECIES_SETBACK_RULES) ---

Часть норм зависит не от типа зоны, а от породы растения: липе у жилого дома
нужно 10 м вместо 5, колючему кустарнику у детской площадки -- 2 м вместо
табличного. Правило задаётся по РОДУ (первое слово названия вида: "Липа
мелколистная" -> "липа"), без учёта регистра и с ё == е -- названия видов в
каталоге и в DXF пишутся по-разному ("Клён"/"Клен", "Берёза"/"Береза"), а
норма в акте сформулирована именно по роду ("липа, клен, дуб...").

Правило по породе может только УЖЕСТОЧИТЬ отступ, но не ослабить его:
setback_for() берёт максимум из табличной нормы и всех подходящих правил.
Так два независимых акта (например СП 82 про колючие растения и общая
таблица СП 42) не могут случайно "перебить" друг друга в мягкую сторону.
"""

from __future__ import annotations

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
    # Одна строка таблицы 9.1 -- "силовой кабель и кабель связи". Парсер
    # различает их по типу (signal_cable -- warning, а не forbidden: ток
    # связи не опасен при раскопке), но отступ посадки от них ОДИН И ТОТ ЖЕ.
    # Раньше signal_cable в этой таблице не было, и setback_for() откатывался
    # на minDistance самой зоны (0.5 м) -- дерево вставало в ~1 м от оси
    # кабеля связи при норме 2 м.
    "electrical": {"tree": 2.0, "bush": 0.7},
    "signal_cable": {"tree": 2.0, "bush": 0.7},
    # "Тепловая сеть (стенка канала, тоннеля или оболочка при бесканальной
    # прокладке)" -- СП 42, табл. 9.1; "теплопровод, трубопровод, теплосеть"
    # -- 743-ПП, табл. 3.6.1 (это и есть "не менее 2 метров от
    # трубопроводов" из ТЗ). Раньше парсер относил теплосеть к type="custom",
    # и эта строка не применялась вовсе.
    "heat_network": {"tree": 2.0, "bush": 1.0},
    "pedestrian_path": {"tree": 0.7, "bush": 0.5},
}


@dataclass(frozen=True)
class SpeciesSetbackRule:
    """Минимальный отступ для растений перечисленных родов от зоны одного
    типа. kinds -- к каким видам посадки применяется (боярышник бывает и
    деревом, и кустарником -- норма СП 82 про колючие растения касается
    обоих)."""

    genera: frozenset[str]
    zone_type: str
    distance_m: float
    kinds: frozenset[PlantKind]
    source: str


def _rule(genera: tuple[str, ...], zone_type: str, distance_m: float, kinds: tuple[PlantKind, ...], source: str):
    return SpeciesSetbackRule(frozenset(genera), zone_type, distance_m, frozenset(kinds), source)


_BOTH: tuple[PlantKind, ...] = ("tree", "bush")

# 743-ПП, табл. 3.6.1, прим. 3: "Деревья с широкой кроной (липа, клен, дуб,
# каштан, тополь и др.), затеняющие жилые помещения, должны сажаться не
# ближе 10 м от здания". Назначение здания (жилое или нет) в DXF не
# размечено, поэтому применяем ко всем зданиям -- строже нормы, не мягче.
WIDE_CROWN_GENERA = ("липа", "клен", "дуб", "каштан", "тополь")
# СП 82.13330.2016, п. 9.22: "Размещение колючих растений (например,
# кустарников розы, барбариса, боярышника) ... допускается на расстоянии не
# менее 2 м от площадок и пешеходных коммуникаций". Шиповник -- та же роза.
THORNY_GENERA = ("роза", "шиповник", "барбарис", "боярышник")
# МГСН 1.02-02 (ППМ 623-ПП), п. 4.2.8: "У теплотрасс не следует размещать:
# липу, клен, сирень, жимолость -- ближе 2 м, тополь, боярышник, кизильник,
# дерен, лиственницу, березу -- ближе 3-4 м" (от оси теплотрассы). Для второй
# группы берём верхнюю границу -- 4 м.
HEAT_SENSITIVE_2M_GENERA = ("липа", "клен", "сирень", "жимолость")
HEAT_SENSITIVE_4M_GENERA = ("тополь", "боярышник", "кизильник", "дерен", "лиственница", "береза")

SPECIES_SETBACK_RULES: tuple[SpeciesSetbackRule, ...] = (
    _rule(WIDE_CROWN_GENERA, "building", 10.0, ("tree",), "ППМ 743-ПП, табл. 3.6.1, прим. 3"),
    _rule(THORNY_GENERA, "pedestrian_path", 2.0, _BOTH, "СП 82.13330.2016, п. 9.22"),
    _rule(THORNY_GENERA, "playground_zone", 2.0, _BOTH, "СП 82.13330.2016, п. 9.22"),
    _rule(HEAT_SENSITIVE_2M_GENERA, "heat_network", 2.0, _BOTH, "МГСН 1.02-02, п. 4.2.8"),
    _rule(HEAT_SENSITIVE_4M_GENERA, "heat_network", 4.0, _BOTH, "МГСН 1.02-02, п. 4.2.8"),
    # Issue #43: агрессивная корневая система тополя и ивы у труб и
    # фундамента. Нормативного источника с числом нет -- экспертная оценка,
    # помечена как таковая (у тополя отступ от здания и так 10 м по 743-ПП).
    _rule(("тополь",), "sewer", 3.0, ("tree",), "экспертная оценка (issue #43)"),
    _rule(("тополь",), "water_pipeline", 3.0, ("tree",), "экспертная оценка (issue #43)"),
    _rule(("ива",), "building", 6.0, ("tree",), "экспертная оценка (issue #43)"),
    _rule(("ива",), "sewer", 3.5, ("tree",), "экспертная оценка (issue #43)"),
    _rule(("ива",), "water_pipeline", 3.5, ("tree",), "экспертная оценка (issue #43)"),
)

# Наибольший отступ из всех таблиц -- на столько вызывающему коду нужно
# расширять зону при грубом отборе кандидатов (STRtree в violation_report.py),
# чтобы не потерять нарушение любой породы.
MAX_SETBACK_M = max(
    max(v for rule in SETBACK_NORMS.values() for v in rule.values()),
    max(rule.distance_m for rule in SPECIES_SETBACK_RULES),
)

# Вид дерева, используемый генератором, когда конкретный species не задан
# явно вызывающим кодом.
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


# Один вид ("Липа мелколистная") или несколько сразу -- когда на одно место
# может встать любой из них (смесь видов генератора, набор каталога в правке
# текстом): тогда действует самое строгое правило среди всех.
SpeciesArg = Optional[str | Iterable[Optional[str]]]


def species_rules(species: SpeciesArg) -> frozenset[SpeciesSetbackRule]:
    """Все правила по породе, которые касаются этого вида (или любого из
    перечисленных). Пусто, если вид не задан или его род ни в одном правиле
    не упомянут. frozenset -- годится как ключ кэша (placement.Placer)."""
    if species is None:
        return frozenset()
    names = [species] if isinstance(species, str) else list(species)
    genera = {genus_of(name) for name in names} - {None}
    return frozenset(rule for rule in SPECIES_SETBACK_RULES if rule.genera & genera)


def setback_for(
    zone_type: str,
    plant_kind: PlantKind,
    zone_min_distance: float,
    species: SpeciesArg = None,
) -> float:
    """Требуемый отступ данного вида посадки от зоны типа zone_type.

    1. Табличная норма SETBACK_NORMS для zone_type/plant_kind. Если тип
       зоны в таблице не описан (нестандартная/custom зона) -- minDistance
       самой зоны как есть, без выдумывания цифр, которые нечем подтвердить.
    2. Если задан species -- максимум из п.1 и всех правил по его роду (или
       родам, если видов несколько) для этого типа зоны и вида посадки:
       правило только ужесточает.
    """
    rule = SETBACK_NORMS.get(zone_type)
    distance = rule[plant_kind] if rule else zone_min_distance
    for species_rule in species_rules(species):
        if species_rule.zone_type == zone_type and plant_kind in species_rule.kinds:
            distance = max(distance, species_rule.distance_m)
    return distance


def plant_kind_of_object_type(object_type: str) -> Optional[PlantKind]:
    if object_type in ("tree", "bush"):
        return object_type  # type: ignore[return-value]
    return None
