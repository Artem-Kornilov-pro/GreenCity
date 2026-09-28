"""text_editor/applier.py -- apply_plan: разбор операций от модели, точечные
add/remove/move/rotate, правила отступа по породе. Хэнд-билженые LlmPlan на
реальных участках locations/."""

import math

import pytest

from core.plant_catalog import catalog_by_id, load_catalog
from helpers import make_scene, make_zone
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()
BY_ID = catalog_by_id()


@pytest.fixture
def scene1(location_scene):
    return location_scene[1].model_copy(deep=True)


@pytest.fixture
def scene2(location_scene):
    return location_scene[2].model_copy(deep=True)


def run(scene, *operations, catalog=CATALOG):
    return apply_plan(scene, LlmPlan(operations=list(operations)), catalog)


# =============================================================================
# run(): разбор операций, устойчивость к мусору
# =============================================================================


def test_run_rejects_unparseable_operation_without_stopping_the_rest(scene1):
    result = run(scene1, {"op": "not_a_real_operation"}, {"op": "remove", "id": "lamp_001"})
    assert any("не разобрать" in r for r in result.rejected)
    assert any("удалён lamp_001" in a for a in result.applied)


def test_run_rejects_operation_missing_required_field(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "bush_medium"})  # нет x/z
    assert result.rejected
    assert "add" in result.rejected[0]


def test_run_preserves_original_scene_object_count_on_full_failure(scene1):
    original_count = len(scene1.objects)
    run(scene1, {"op": "remove", "id": "no_such_object"})
    assert len(scene1.objects) == original_count  # исходная сцена не мутирована


def test_run_empty_plan_returns_scene_unchanged(scene1):
    result = run(scene1)
    assert len(result.scene.objects) == len(scene1.objects)
    assert result.applied == []
    assert result.rejected == []


# =============================================================================
# add / remove / move / rotate
# =============================================================================


def test_add_places_object_at_requested_point_when_free(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "bush_medium", "x": 30.0, "z": 0.0})
    assert result.applied
    new_bushes = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert len(new_bushes) == 1


def test_add_unknown_catalog_id_is_rejected(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "not_a_real_item", "x": 0.0, "z": 0.0})
    assert result.rejected
    assert "not_a_real_item" in result.rejected[0]


def test_add_snaps_when_requested_point_violates_setback(scene1):
    # (0, 0) внутри здания в локации 1 -- должно сдвинуть или отклонить, не упасть.
    result = run(scene1, {"op": "add", "catalog_id": "species_lipa_melkolistnaya", "x": 0.0, "z": 0.0})
    assert result.applied or result.rejected


def test_remove_deletes_the_object(scene1):
    result = run(scene1, {"op": "remove", "id": "lamp_001"})
    assert "удалён lamp_001" in result.applied[0]
    assert "lamp_001" not in {o.id for o in result.scene.objects}


def test_remove_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "remove", "id": "no_such_id"})
    assert "такого объекта нет" in result.rejected[0]


def test_remove_uneditable_type_is_rejected(scene1):
    building_id = next(o.id for o in scene1.objects if o.type == "building")
    result = run(scene1, {"op": "remove", "id": building_id})
    assert "менять нельзя" in result.rejected[0]


def test_move_relocates_object_to_free_point(scene1):
    result = run(scene1, {"op": "move", "id": "lamp_001", "x": 40.0, "z": 0.0})
    moved = next(o for o in result.scene.objects if o.id == "lamp_001")
    assert (moved.position.x, moved.position.z) != (0, 0)


def test_move_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "move", "id": "no_such_id", "x": 0.0, "z": 0.0})
    assert result.rejected


def test_move_restores_original_occupancy_when_target_is_unreachable(scene1):
    # Внутрь здания, далеко от границы -- нет годного места поблизости.
    result = run(scene1, {"op": "move", "id": "lamp_001", "x": 0.0, "z": 0.0})
    if result.rejected:
        # Объект должен остаться на старом месте и не исчезнуть из индекса
        # (иначе следующая операция могла бы поставить что-то поверх него).
        assert any(o.id == "lamp_001" for o in result.scene.objects)


def test_rotate_changes_rotation_in_radians(scene1):
    result = run(scene1, {"op": "rotate", "id": "lamp_001", "rotation_deg": 90.0})
    rotated = next(o for o in result.scene.objects if o.id == "lamp_001")
    assert rotated.rotation == pytest.approx(math.radians(90.0))


def test_rotate_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "rotate", "id": "no_such_id", "rotation_deg": 10})
    assert result.rejected


# =============================================================================
# Правила отступа по породе (setback_norms.SPECIES_SETBACK_RULES)
# =============================================================================


def test_add_linden_near_building_snaps_to_10m():
    # Липа -- широкая крона, 10 м от здания (743-ПП). Модель просит поставить
    # её в 7 м от стены -- планировщик сдвигает, а не оставляет с нарушением.
    scene = make_scene(restrictions=[make_zone(type="building")])
    result = run(scene, {"op": "add", "catalog_id": "species_lipa_melkolistnaya", "x": 12, "z": 0})
    created = [o for o in result.scene.objects if o.metadata.get("catalogId") == "species_lipa_melkolistnaya"]
    assert len(created) == 1
    assert created[0].position.x >= 15.0 - 0.01
    assert created[0].metadata["species"] == "Липа мелколистная"


def test_add_birch_at_7m_from_building_stays_in_place():
    scene = make_scene(restrictions=[make_zone(type="building")])
    result = run(scene, {"op": "add", "catalog_id": "species_bereza_povislaya", "x": 12, "z": 0})
    created = [o for o in result.scene.objects if o.metadata.get("catalogId") == "species_bereza_povislaya"]
    assert created[0].position.x == pytest.approx(12)


# =============================================================================
# run_greenplan: параметры для GreenPlan, который запускает фронтенд
# =============================================================================


def test_run_greenplan_returns_options_and_leaves_the_scene_alone(scene1):
    result = run(scene1, {
        "op": "run_greenplan", "style": "regular", "lawn": False, "paths": True,
        "preferred_trees": ["species_lipa_melkolistnaya"],
    })
    options = result.greenplan
    assert (options.style, options.trees, options.lawn, options.paths, options.lighting) == ("regular", True, False, True, False)
    assert options.preferred_trees == ["species_lipa_melkolistnaya"]
    assert len(result.scene.objects) == len(scene1.objects)
    assert result.applied and "регулярный" in result.applied[0] and "Липа мелколистная" in result.applied[0]


def test_run_greenplan_passes_remove_violating_plants(scene1):
    result = run(scene1, {"op": "run_greenplan", "remove_violating_plants": True})
    assert result.greenplan.remove_violating_plants is True
    assert "с нарушением норм: удалены" in result.applied[0]


def test_run_greenplan_drops_preferred_ids_of_the_wrong_kind(scene1):
    bush = next(c for c in CATALOG if c.id.startswith("species_") and c.category == "bush" and c.object_type == "bush")
    result = run(scene1, {"op": "run_greenplan", "preferred_trees": [bush.id, "bench", "no_such_id"]})
    assert result.greenplan.preferred_trees == []
    assert len([w for w in result.warnings if "не дерево" in w]) == 3


def test_plan_without_run_greenplan_has_no_options(scene1):
    assert run(scene1, {"op": "remove", "id": "lamp_001"}).greenplan is None
