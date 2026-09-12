"""setback_norms.setback_for -- порядок разрешения отступа: species-override
(только для деревьев) -> общая норма по типу зоны -> minDistance самой зоны,
если тип зоны не описан в таблице вовсе."""

from setback_norms import (
    DEFAULT_TREE_SPECIES,
    SETBACK_NORMS,
    SPECIES_SETBACK_OVERRIDES,
    plant_kind_of_object_type,
    setback_for,
)


def test_general_norm_for_known_zone_type():
    assert setback_for("building", "tree", zone_min_distance=0.0) == SETBACK_NORMS["building"]["tree"]
    assert setback_for("building", "bush", zone_min_distance=0.0) == SETBACK_NORMS["building"]["bush"]


def test_unknown_zone_type_falls_back_to_zone_min_distance():
    # "custom" не описан в SETBACK_NORMS -- ни для дерева, ни для куста
    assert setback_for("custom_fence", "tree", zone_min_distance=3.5) == 3.5
    assert setback_for("custom_fence", "bush", zone_min_distance=1.2) == 1.2


def test_species_override_applies_only_to_trees(monkeypatch):
    monkeypatch.setitem(SPECIES_SETBACK_OVERRIDES, "тополь чёрный", {"building": 8.0})
    assert setback_for("building", "tree", zone_min_distance=0.0, species="тополь чёрный") == 8.0
    # У куста species не учитывается вовсе, даже если бы для него была запись
    assert setback_for("building", "bush", zone_min_distance=0.0, species="тополь чёрный") == SETBACK_NORMS["building"]["bush"]


def test_species_override_missing_zone_type_falls_back_to_general_norm(monkeypatch):
    # Override есть для вида, но не для ЭТОГО типа зоны -- общая норма для tree
    monkeypatch.setitem(SPECIES_SETBACK_OVERRIDES, "берёза повислая", {"sewer": 3.0})
    assert setback_for("building", "tree", zone_min_distance=0.0, species="берёза повислая") == SETBACK_NORMS["building"]["tree"]


def test_species_override_empty_dict_is_same_as_no_override(monkeypatch):
    monkeypatch.setitem(SPECIES_SETBACK_OVERRIDES, "неизвестный вид", {})
    assert setback_for("building", "tree", zone_min_distance=0.0, species="неизвестный вид") == SETBACK_NORMS["building"]["tree"]


def test_default_species_has_no_overrides_currently():
    # Каталог видов пока пуст (см. докстринг модуля) -- поведение с дефолтным
    # видом должно быть тождественно поведению без species вовсе.
    assert DEFAULT_TREE_SPECIES not in SPECIES_SETBACK_OVERRIDES
    without_species = setback_for("gas_pipeline", "tree", zone_min_distance=0.0)
    with_default_species = setback_for("gas_pipeline", "tree", zone_min_distance=0.0, species=DEFAULT_TREE_SPECIES)
    assert without_species == with_default_species


def test_plant_kind_of_object_type():
    assert plant_kind_of_object_type("tree") == "tree"
    assert plant_kind_of_object_type("bush") == "bush"
    assert plant_kind_of_object_type("bench") is None
    assert plant_kind_of_object_type("lawn_patch") is None
