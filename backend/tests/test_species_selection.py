"""species_selection.py -- подбор видов для GreenPlan по ассортименту Москвы,
форме под паттерн и нормам (369-ПП, коды ассортимента, СП 82 п. 9.22,
743-ПП прим. 3)."""

import pytest
from invasive_species import invasive_match
from pattern_library import PATTERN_LIBRARY
from plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, load_catalog
from setback_norms import THORNY_GENERA, genus_of, setback_for
from species_selection import (
    PATTERN_ROLES,
    TERRITORY_COLUMN,
    SiteContext,
    _expand_names,
    assortment_entry,
    model_group,
    select_species,
)

CATALOG = load_catalog()
TREES = [c for c in CATALOG if c.category == "tree"]
BUSHES = [c for c in CATALOG if c.category == "bush" and c.object_type == "bush"]
TERRITORIES = list(TERRITORY_COLUMN)


def _site(territory="двор", playground=False, key="site-1"):
    return SiteContext(territory_type=territory, has_playground=playground, site_key=key)


def _all_palettes(site):
    for pattern_id, spec in PATTERN_LIBRARY.items():
        for zone_kind in spec.zone_kinds:
            yield zone_kind, pattern_id, select_species(zone_kind, pattern_id, TREES, BUSHES, site)


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        ("Липа мелколистная/сердцевидная (формы и сорта)", ["липа мелколистная", "липа сердцевидная"]),
        ("Береза повислая/плакучая (формы и сорта)", ["береза повислая", "береза плакучая"]),
        ("Слива колючая/терн", ["слива колючая", "терн"]),
        ("Тополь дрожащий/осина (муж. клоны)", ["тополь дрожащий", "осина"]),
        ("Клен гиннала/приречный", ["клен гиннала", "клен приречный"]),
        ("Тополь бальзамический (муж. клоны)", ["тополь бальзамический"]),
        ("Ель колючая", ["ель колючая"]),
    ],
)
def test_expand_names_handles_synonyms_and_notes(raw, names):
    assert _expand_names(raw) == names


def test_every_pattern_has_roles():
    assert set(PATTERN_ROLES) == set(PATTERN_LIBRARY)


@pytest.mark.parametrize("territory", TERRITORIES)
def test_only_assortment_species_for_the_territory_are_chosen(territory):
    # Базовые модели ("Дерево -- среднее") и заглушки из примечаний не
    # попадают никогда -- у них нет строки в ассортименте.
    site = _site(territory)
    column = TERRITORY_COLUMN[territory]
    for _zone_kind, pattern_id, palette in _all_palettes(site):
        for item in (*palette.trees, *palette.bushes):
            entry = assortment_entry(item)
            assert entry is not None, (pattern_id, item.label)
            assert entry.territories.get(column) == "+", (territory, pattern_id, item.label)
            assert item.id.startswith("species_"), item.label
            assert not model_group(item).startswith("vine_")


def test_every_role_gets_species_on_the_real_catalog():
    for zone_kind, pattern_id, palette in _all_palettes(_site("улица")):
        assert palette.trees and palette.bushes, (zone_kind, pattern_id)
        assert "вне ассортимента" not in palette.basis


def test_no_invasive_no_self_seeding_species():
    for _, _, palette in _all_palettes(_site("парк_сквер")):
        for item in (*palette.trees, *palette.bushes):
            assert invasive_match(item.label) is None
            assert "7" not in assortment_entry(item).codes


def test_code_2_species_excluded_only_when_site_has_playground():
    def chosen_codes(playground):
        return {
            code
            for _, _, palette in _all_palettes(_site("двор", playground=playground))
            for item in (*palette.trees, *palette.bushes)
            for code in assortment_entry(item).codes
        }

    assert "2" not in chosen_codes(playground=True)


def test_path_corridor_gets_no_thorny_or_reagent_sensitive_species():
    for territory in TERRITORIES:
        palette = select_species("path_corridor", "linear_hedge_row", TREES, BUSHES, _site(territory))
        for item in (*palette.trees, *palette.bushes):
            assert genus_of(item.label) not in THORNY_GENERA
            assert "5" not in assortment_entry(item).codes


def test_building_border_gets_no_species_needing_more_than_table_setback():
    # Липа/клён/тополь -- 10 м от здания (743-ПП) -- в полосу у здания не помещаются.
    for territory in TERRITORIES:
        palette = select_species("building_border", "building_ring", TREES, BUSHES, _site(territory))
        for item in palette.trees:
            assert setback_for("building", "tree", 0.0, species=item.label) == setback_for("building", "tree", 0.0)


@pytest.mark.parametrize("pattern_id", list(PATTERN_ROLES))
def test_species_count_matches_role_and_genera_are_distinct(pattern_id):
    tree_role, bush_role = PATTERN_ROLES[pattern_id]
    zone_kind = next(iter(PATTERN_LIBRARY[pattern_id].zone_kinds))
    palette = select_species(zone_kind, pattern_id, TREES, BUSHES, _site("парк_сквер"))
    assert len(palette.trees) == tree_role.count
    assert len(palette.bushes) == bush_role.count
    # На реальном каталоге разных родов хватает -- смесь не из одного рода.
    for items in (palette.trees, palette.bushes):
        assert len({genus_of(i.label) for i in items}) == len(items)


def test_hedge_and_alley_are_single_species():
    palette = select_species("path_corridor", "linear_hedge_row", TREES, BUSHES, _site())
    assert len(palette.trees) == 1 and len(palette.bushes) == 1


def test_selection_is_deterministic_per_site_and_varies_between_sites():
    first = select_species("open_area", "poisson_scatter_fill", TREES, BUSHES, _site(key="A"))
    again = select_species("open_area", "poisson_scatter_fill", TREES, BUSHES, _site(key="A"))
    assert [i.label for i in first.trees] == [i.label for i in again.trees]
    variants = {
        tuple(i.label for i in select_species("open_area", "poisson_scatter_fill", TREES, BUSHES, _site(key=k)).bushes)
        for k in ("A", "B", "C", "D", "E", "F")
    }
    assert len(variants) > 1


def test_courtyard_prefers_515_base_assortment():
    palette = select_species("path_corridor", "linear_hedge_row", TREES, BUSHES, _site("двор"))
    assert "515-ПП" in palette.basis


def test_catalog_outside_assortment_falls_back_to_given_items_and_says_so():
    fake = CatalogItem(
        id="fake_tree", category="tree", label="Дерево-заглушка", setback_kind="tree", object_type="tree",
        model="/models/x.glb", dimensions=CatalogItemDimensions(height=3.0, radius=1.0),
        render=CatalogItemRender(shape="cone", color="#000000"),
    )
    palette = select_species("open_area", "generic_fill", [fake], [], _site())
    assert palette.trees == [fake]
    assert "вне ассортимента" in palette.basis
