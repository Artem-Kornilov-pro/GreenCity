"""Объекты из чертежа за границей участка (metadata.offsite,
parse_dxf._mark_offsite_points) -- невидимые ограничения: расстановка
соблюдает норму до них, а проверка нарушений, отчёты и правка текстом их
не видят."""

import json

from core.placement import Placer
from core.plant_catalog import load_catalog
from greenplan.violation_report import find_violations
from greenplan.visualization import site_brief
from helpers import make_object, make_scene, make_zone, rect_points
from text_editor.prompt import _build_context
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()
OFFSITE = {"offsite": True, "sourceLayer": "Деревья"}


def _scene():
    # Участок 100x100 м; за его восточной границей (x = 50) -- здание соседей,
    # у которого дерево из чертежа стоит ближе нормы, и фонарь в 2 м.
    building = make_zone("building_n", "building", polygon=rect_points(56, -5, 66, 5))
    return make_scene(
        restrictions=[building],
        objects=[
            make_object("tree_in", "tree", 0.0, 0.0, metadata={"sourceLayer": "Деревья"}),
            make_object("tree_off", "tree", 53.0, 0.0, metadata=dict(OFFSITE)),
            make_object("lamp_off", "lamp", 52.0, 20.0, metadata=dict(OFFSITE)),
        ],
    )


def test_offsite_plant_violations_are_not_reported():
    scene = _scene()
    assert [v.object_id for v in find_violations(scene)] == []
    # Без пометки то же дерево -- нарушение (5 м от здания).
    scene.objects[1].metadata.pop("offsite")
    assert [v.object_id for v in find_violations(scene)] == ["tree_off"]


def test_offsite_objects_still_constrain_placement():
    placer = Placer(_scene())
    # Дерево у границы участка в 2 м от фонаря за ней -- нарушение нормы 4 м.
    assert placer.blocker(49.0, 18.5, "tree") is not None
    assert placer.blocker(40.0, 18.5, "tree") is None


def test_text_editor_does_not_touch_offsite_objects():
    result = apply_plan(_scene(), LlmPlan(operations=[{"op": "remove_where", "object_types": ["tree", "lamp"]}]), CATALOG)
    assert sorted(o.id for o in result.scene.objects) == ["lamp_off", "tree_off"]

    result = apply_plan(_scene(), LlmPlan(operations=[{"op": "remove", "id": "tree_off"}]), CATALOG)
    assert any(o.id == "tree_off" for o in result.scene.objects)
    assert "такого объекта нет" in result.rejected[0]


def test_text_editor_prompt_and_site_brief_skip_offsite_objects():
    scene = _scene()
    context = json.loads(_build_context(scene, CATALOG, Placer(scene)))
    assert [o["id"] for o in context["objects"]] == ["tree_in"]
    assert context["object_counts"] == {"tree": 1}
    assert site_brief(scene, []).existing_trees == 1
