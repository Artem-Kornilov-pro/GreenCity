"""setback_norms.setback_for -- табличная норма по типу зоны (СП 42.13330,
табл. 9.1) -> minDistance самой зоны, если тип не описан, плюс правила по
роду растения (743-ПП, СП 82, МГСН 1.02-02), которые только ужесточают."""

import csv
from pathlib import Path

import pytest
from setback_norms import (
    DEFAULT_TREE_SPECIES,
    MAX_SETBACK_M,
    SETBACK_NORMS,
    SPECIES_SETBACK_RULES,
    genus_of,
    plant_kind_of_object_type,
    setback_for,
    species_rules,
)

SP42_CSV = Path(__file__).resolve().parents[2] / "data" / "norms" / "sp-42-13330-2016" / "setbacks.csv"


def test_general_norm_for_known_zone_type():
    assert setback_for("building", "tree", zone_min_distance=0.0) == SETBACK_NORMS["building"]["tree"]
    assert setback_for("building", "bush", zone_min_distance=0.0) == SETBACK_NORMS["building"]["bush"]


def test_unknown_zone_type_falls_back_to_zone_min_distance():
    # "custom" не описан в SETBACK_NORMS -- ни для дерева, ни для куста
    assert setback_for("custom_fence", "tree", zone_min_distance=3.5) == 3.5
    assert setback_for("custom_fence", "bush", zone_min_distance=1.2) == 1.2


def test_code_table_matches_sp42_table_9_1_csv():
    # data/norms/sp-42-13330-2016/setbacks.csv -- дословный перенос таблицы
    # 9.1 из официального текста. Каждый наш тип зоны, привязанный к строке
    # таблицы, обязан давать ровно её числа (прочерк -- не нормируется, 0).
    # Именно так была бы поймана ошибка, из-за которой кабель связи получал
    # 0.5 м вместо 2 м: signal_cable не было ни в таблице, ни в CSV.
    checked = set()
    with SP42_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for zone_type in filter(None, row["zone_type"].split(";")):
                if zone_type == "lamp":  # точечный объект, не зона -- см. placement.POINT_CLEARANCE_M
                    continue
                expected_tree = float(row["tree_m"]) if row["tree_m"] else 0.0
                expected_bush = float(row["bush_m"]) if row["bush_m"] else 0.0
                assert SETBACK_NORMS[zone_type] == {"tree": expected_tree, "bush": expected_bush}, zone_type
                checked.add(zone_type)
    # И наоборот: в коде нет строки, не подкреплённой таблицей.
    assert checked == set(SETBACK_NORMS)


@pytest.mark.parametrize("zone_type", ["signal_cable", "electrical"])
def test_signal_cable_has_same_setback_as_power_cable(zone_type):
    # СП 42, табл. 9.1 -- "силовой кабель и кабель связи", одна строка.
    assert setback_for(zone_type, "tree", zone_min_distance=0.5) == 2.0
    assert setback_for(zone_type, "bush", zone_min_distance=0.5) == 0.7


def test_heat_network_uses_its_table_row_not_zone_min_distance():
    # 743-ПП, табл. 3.6.1 -- "теплопровод, трубопровод, теплосеть" 2.0 / 1.0.
    assert setback_for("heat_network", "tree", zone_min_distance=2.0) == 2.0
    assert setback_for("heat_network", "bush", zone_min_distance=2.0) == 1.0


@pytest.mark.parametrize(
    ("species", "genus"),
    [
        ("Липа мелколистная", "липа"),
        ("Клён остролистный", "клен"),
        ("КЛЕН ОСТРОЛИСТНЫЙ", "клен"),
        ("Берёза полезная", "береза"),
        ("Тополь (дерево, высокое, колонновидное, 8.0 м)", "тополь"),
        ("", None),
        (None, None),
    ],
)
def test_genus_of_normalizes_case_and_yo(species, genus):
    assert genus_of(species) == genus


@pytest.mark.parametrize("species", ["Липа мелколистная", "Клён остролистный", "Дуб черешчатый", "Каштан конский", "Тополь чёрный"])
def test_wide_crown_trees_need_10m_from_building(species):
    # 743-ПП, табл. 3.6.1, прим. 3.
    assert setback_for("building", "tree", zone_min_distance=0.0, species=species) == 10.0


def test_other_trees_keep_the_table_building_setback():
    assert setback_for("building", "tree", zone_min_distance=0.0, species="Берёза полезная") == 5.0
    assert setback_for("building", "tree", zone_min_distance=0.0, species="Рябина обыкновенная") == 5.0


def test_wide_crown_rule_applies_to_trees_only():
    assert setback_for("building", "bush", zone_min_distance=0.0, species="Липа мелколистная") == 1.5


@pytest.mark.parametrize("species", ["Роза морщинистая", "Барбарис Тунберга", "Боярышник обыкновенный", "Шиповник"])
@pytest.mark.parametrize("zone_type", ["pedestrian_path", "playground_zone"])
def test_thorny_plants_need_2m_from_paths_and_playgrounds(species, zone_type):
    # СП 82.13330.2016, п. 9.22 -- и для куста, и для дерева (боярышник бывает деревом).
    assert setback_for(zone_type, "bush", zone_min_distance=0.5, species=species) == 2.0
    assert setback_for(zone_type, "tree", zone_min_distance=0.5, species=species) == 2.0


def test_heat_network_species_rules_from_mgsn():
    # МГСН 1.02-02, п. 4.2.8: сирень/жимолость -- 2 м, кизильник/дерен/береза -- 3-4 м (берём 4).
    assert setback_for("heat_network", "bush", zone_min_distance=2.0, species="Сирень обыкновенная") == 2.0
    assert setback_for("heat_network", "bush", zone_min_distance=2.0, species="Кизильник блестящий") == 4.0
    assert setback_for("heat_network", "bush", zone_min_distance=2.0, species="Дёрен белый") == 4.0
    assert setback_for("heat_network", "tree", zone_min_distance=2.0, species="Берёза повислая") == 4.0
    # Нет правила -- табличная норма
    assert setback_for("heat_network", "bush", zone_min_distance=2.0, species="Спирея серая") == 1.0


def test_species_rule_never_loosens_the_table_norm():
    # Правило для тополя у канализации -- 3 м, табличная норма 1.5: берётся
    # максимум. Проверяем обратное -- правило меньше табличной нормы не
    # может её ослабить.
    for rule in SPECIES_SETBACK_RULES:
        for kind in rule.kinds:
            for genus in rule.genera:
                table = setback_for(rule.zone_type, kind, zone_min_distance=0.0)
                assert setback_for(rule.zone_type, kind, zone_min_distance=0.0, species=genus) >= table


def test_several_species_take_the_strictest_rule():
    # Набор видов (смесь генератора, пул каталога в правке текстом): на
    # любое место может встать любой из них.
    mix = ["Берёза полезная", "Липа мелколистная"]
    assert setback_for("building", "tree", zone_min_distance=0.0, species=mix) == 10.0
    assert setback_for("building", "tree", zone_min_distance=0.0, species=["Берёза полезная"]) == 5.0


def test_species_rules_is_hashable_cache_key():
    assert species_rules("Липа мелколистная") == species_rules(["Липа крупнолистная"])
    assert species_rules("Рябина обыкновенная") == frozenset()
    assert species_rules(None) == frozenset()


def test_poplar_and_willow_expert_estimates_still_apply():
    # Issue #43 -- экспертная оценка, не норма акта; у тополя от здания теперь 10 м по 743-ПП.
    assert setback_for("sewer", "tree", zone_min_distance=0.0, species="Тополь чёрный") == 3.0
    assert setback_for("water_pipeline", "tree", zone_min_distance=0.0, species="Тополь бальзамический") == 3.0
    assert setback_for("building", "tree", zone_min_distance=0.0, species="Ива белая") == 6.0
    assert setback_for("sewer", "tree", zone_min_distance=0.0, species="Ива ломкая") == 3.5
    # gas_pipeline осознанно не переопределён -- общая норма tree применяется
    assert setback_for("gas_pipeline", "tree", zone_min_distance=0.0, species="Тополь чёрный") == SETBACK_NORMS["gas_pipeline"]["tree"]


def test_every_species_rule_names_its_source():
    for rule in SPECIES_SETBACK_RULES:
        assert rule.source.strip()


def test_max_setback_covers_every_rule():
    assert MAX_SETBACK_M == 10.0
    assert all(rule.distance_m <= MAX_SETBACK_M for rule in SPECIES_SETBACK_RULES)


def test_default_species_is_a_wide_crown_tree():
    # Липа мелколистная -- основной вид генератора, и у неё 10 м от здания.
    assert setback_for("building", "tree", zone_min_distance=0.0, species=DEFAULT_TREE_SPECIES) == 10.0


def test_plant_kind_of_object_type():
    assert plant_kind_of_object_type("tree") == "tree"
    assert plant_kind_of_object_type("bush") == "bush"
    assert plant_kind_of_object_type("bench") is None
    assert plant_kind_of_object_type("lawn_patch") is None
