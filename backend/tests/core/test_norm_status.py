"""Статус нормы отступа (issue #108): сверено / допущение / значение сервиса."""

import pytest

from core.setback_norms import NORM_STATUS_LABELS, SETBACK_NORMS, setback_basis, setback_for, setback_reference
from greenplan.explanations import RejectionStats, explanations_json, not_checked
from helpers import make_scene, make_zone


@pytest.mark.parametrize("zone_type", sorted(SETBACK_NORMS))
def test_table_values_are_verified(zone_type):
    ref = setback_reference(zone_type, "tree", 1.0)
    assert ref.status == "verified" and "СП 42.13330.2016" in ref.source


def test_dash_in_the_table_is_verified_as_not_regulated():
    ref = setback_reference("gas_pipeline", "bush", 1.0)
    assert (ref.distance_m, ref.status) == (0.0, "verified")
    assert "не нормируется" in ref.text()


@pytest.mark.parametrize("zone_type", ["playground_zone", "transformer", "custom"])
def test_zone_without_a_norm_is_a_service_value(zone_type):
    ref = setback_reference(zone_type, "tree", 1.5)
    assert (ref.distance_m, ref.status) == (1.5, "service")
    assert ref.text().startswith("значение сервиса") and "нормы нет" in ref.text()


def test_overhead_line_is_an_assumption_because_of_the_crown():
    ref = setback_reference("overhead_power_line", "tree", 2.0, voltage_kv=10)
    assert ref.status == "assumption" and "ПУЭ" in ref.source and "радиус кроны" in ref.note
    assert "принято 10 кВ" in setback_reference("overhead_power_line", "tree", 2.0).text()


def test_expert_and_range_choices_are_assumptions():
    poplar = setback_reference("sewer", "tree", 3.0, species="Тополь берлинский")
    assert (poplar.distance_m, poplar.status) == (3.0, "assumption")
    birch = setback_reference("heat_network", "tree", 2.0, species="Береза повислая")
    assert (birch.distance_m, birch.status) == (4.0, "assumption") and "3–4 м" in birch.note


def test_species_rule_from_an_act_is_verified():
    linden = setback_reference("building", "tree", 1.5, species="Липа мелколистная")
    assert (linden.distance_m, linden.status) == (10.0, "verified")


def test_reference_and_setback_for_always_agree():
    for zone_type in [*SETBACK_NORMS, "overhead_power_line", "playground_zone"]:
        for kind in ("tree", "bush"):
            for species in (None, "Тополь берлинский", "Липа мелколистная", "Береза повислая"):
                for barrier in (False, True):
                    expected = setback_for(zone_type, kind, 2.0, species=species, root_barrier=barrier)
                    assert setback_reference(zone_type, kind, 2.0, species, barrier).distance_m == expected
                    assert setback_basis(zone_type, kind, 2.0, species, barrier)[0] == expected


def test_status_labels_cover_all_statuses():
    assert set(NORM_STATUS_LABELS) == {"verified", "assumption", "service"}


def test_not_checked_lists_zones_without_a_norm():
    scene = make_scene(restrictions=[
        make_zone("b1", "building"),
        make_zone("p1", "playground_zone", "Площадка", severity="warning", min_distance=1.0),
        make_zone("p2", "playground_zone", "Площадка 2", severity="warning", min_distance=1.0),
    ])
    assert [row["what"] for row in not_checked(scene)] == ["детская площадка"]
    scene.meta.origin = {"x": 0.0, "y": 0.0}
    assert explanations_json(scene, [], RejectionStats())["not_checked"] == not_checked(scene)
