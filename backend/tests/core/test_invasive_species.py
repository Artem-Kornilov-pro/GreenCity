"""core/invasive_species.py -- перечень ППМ 369-ПП (приложение 1) и исключение
этих видов из каталога посадок."""

import csv

import pytest

from core.invasive_species import all_invasive_species, invasive_match, normalize_species_name
from core.paths import NORMS_DIR
from core.plant_catalog import load_catalog

NORMS = NORMS_DIR


def test_list_has_all_32_species_in_four_groups():
    species = all_invasive_species()
    assert len(species) == 32
    by_group = {}
    for item in species:
        by_group[item.group] = by_group.get(item.group, 0) + 1
    assert by_group == {"I": 6, "II": 1, "III": 14, "IV": 11}


@pytest.mark.parametrize(
    ("name", "number"),
    [
        ("Дуб красный", "3.4"),
        ("Роза морщинистая (формы и сорта)", "3.11"),
        ("Шиповник морщинистый", "3.11"),
        ("Роза ругоза", "3.11"),  # то же Rosa rugosa под названием из 515-ПП
        ("Клён ясенелистный", "2.1"),  # ё == е
        ('Робиния псевдоакация "Белая акация"', "3.10"),
        ("Дерен белый 'Элегантиссима'", "3.3"),
    ],
)
def test_invasive_names_match(name, number):
    assert invasive_match(name).number == number


@pytest.mark.parametrize("name", ["Дуб черешчатый", "Клен остролистный", "Роза собачья", "Ирга канадская", "", None])
def test_other_species_of_same_genus_are_allowed(name):
    # Запрет -- по виду, а не по роду: дуб красный нельзя, дуб черешчатый можно.
    assert invasive_match(name) is None


def test_normalize_species_name():
    assert normalize_species_name("  Черёмуха  Виргинская (формы и сорта) ") == "черемуха виргинская"


def test_catalog_offers_no_invasive_plants():
    # Ни GreenPlan, ни правка текстом, ни панель "Добавить объект" не должны
    # предлагать вид из перечня 369-ПП.
    offenders = [item.label for item in load_catalog() if item.setback_kind and invasive_match(item.label)]
    assert offenders == []


def test_catalog_still_offers_regular_species():
    labels = {item.label for item in load_catalog()}
    assert {"Липа мелколистная", "Дуб черешчатый", "Клен остролистный"} <= labels


def test_documented_conflicts_with_customer_assortment_are_real():
    # data/norms/369-pp/invasive.yaml перечисляет виды ассортимента заказчика,
    # которые 369-ПП запрещает. Список должен совпадать с тем, что реально
    # находится в CSV: иначе либо конфликт устарел, либо появился новый.
    with (NORMS / "assortment-msk" / "main_assortment.csv").open(encoding="utf-8") as f:
        in_assortment = {row["name"] for row in csv.DictReader(f) if invasive_match(row["name"])}
    assert in_assortment == {
        "Дуб красный",
        "Орех маньчжурский",
        "Черемуха виргинская (формы и сорта)",
        "Аморфа кустарниковая",
        "Ирга колосистая",
        "Пузыреплодник калинолистный (формы и сорта)",
        "Роза морщинистая (формы и сорта)",
        "Рябинник рябинолистный (формы и сорта)",
        "Девичий виноград пятилисточковый",  # needs_check -- см. invasive.yaml
    }
