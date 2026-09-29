"""Параметры GreenPlan (greenplan/options.py): стиль, что сажать,
предпочтительные виды, благоустройство -- и запуск целиком (pipeline.py)."""

import math

from shapely.geometry import Point
from shapely.ops import unary_union

from core.plant_catalog import catalog_by_id, load_catalog
from core.schemas import Point3, SceneObject
from core.shapes import polygon_from_points
from greenplan import pattern_assignment
from greenplan.assortment_report import improvements_assortment
from greenplan.improvements import PATH_LAYER, is_greenplan_zone, plan_improvements
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import assign_patterns
from greenplan.pattern_corpus import PatternRecord
from greenplan.pattern_library import PATTERN_LIBRARY
from greenplan.pattern_retrieval import NeighborMatch
from greenplan.pipeline import remove_violating_plants, run_greenplan
from greenplan.site_characterization import SiteCharacteristics
from greenplan.species_selection import (
    PATTERN_ROLES,
    TERRITORY_COLUMN,
    SiteContext,
    assortment_entry,
    model_group,
    preference_note,
    select_species,
)
from greenplan.violation_report import find_violations
from greenplan.zone_partitioning import GeometricZone
from helpers import make_scene, make_zone, rect_points

CATALOG = load_catalog()
BY_ID = catalog_by_id()
TREES = [c for c in CATALOG if c.category == "tree"]
BUSHES = [c for c in CATALOG if c.category == "bush" and c.object_type == "bush"]
ALL_IMPROVEMENTS = GreenPlanOptions(paths=True, lighting=True, benches=True)


def _site(preferred=()):
    return SiteContext(territory_type="двор", has_playground=False, site_key="s", preferred=frozenset(preferred))


# --- Стиль, заданный пользователем --------------------------------------------


def _with_records(monkeypatch, neighbors):
    monkeypatch.setattr(
        pattern_assignment, "nearest_projects",
        lambda characteristics, k: [NeighborMatch(slug=s, similarity=sim) for s, sim, _ in neighbors],
    )
    monkeypatch.setattr(
        pattern_assignment, "load_pattern_log",
        lambda: {s: {kind: PatternRecord(pattern=p, source_quote="q") for kind, p in r.items()} for s, _, r in neighbors},
    )


def _open(zone_id, area=10.0):
    return GeometricZone(id=zone_id, kind="open_area", polygon=[], area_sqm=area)


def test_user_style_overrides_the_vote(monkeypatch):
    # Голосование дало бы пейзажный стиль; пользователь выбрал регулярный.
    _with_records(monkeypatch, [("waves", 0.9, {"open_area": "flowing_rows"}), ("bosque", 0.4, {"open_area": "formal_bosque_grid"})])
    assignments = assign_patterns(None, [_open("a")], characteristics=SiteCharacteristics.model_construct(), style="regular")
    assert assignments[0].site_style == "regular" and assignments[0].lead_project == "bosque"
    assert PATTERN_LIBRARY[assignments[0].pattern_id].style in ("regular", "neutral")


def test_user_style_without_analogues_uses_style_defaults(monkeypatch):
    _with_records(monkeypatch, [("waves", 0.9, {"open_area": "flowing_rows"})])
    (a,) = assign_patterns(None, [_open("a")], characteristics=SiteCharacteristics.model_construct(), style="regular")
    assert (a.site_style, a.lead_project, a.pattern_id, a.source_project) == ("regular", None, "triangular_grid_fill", None)


# --- Предпочтительные виды ----------------------------------------------------


def test_preferred_species_comes_first_even_outside_the_role_form():
    # Вид из ассортимента для дворов, чья форма роще не подходит (например,
    # колонновидный): без предпочтения в рощу он не попал бы вовсе.
    grove_groups = PATTERN_ROLES["grove_clusters"][0].groups
    column = TERRITORY_COLUMN["двор"]
    other_form = next(
        t for t in TREES
        if assortment_entry(t) and assortment_entry(t).territories.get(column) == "+"
        and model_group(t) and model_group(t) not in grove_groups and not model_group(t).startswith("vine_")
        and preference_note(t, ["open_area"], _site()).endswith("не нашлось места с соблюдением норм")
    )
    plain = select_species("open_area", "grove_clusters", TREES, [], _site())
    preferred = select_species("open_area", "grove_clusters", TREES, [], _site([other_form.label]))
    assert other_form.label not in [t.label for t in plain.trees]
    assert preferred.trees[0].label == other_form.label


def test_preferred_species_still_obeys_the_norms():
    # Колючий кустарник вдоль дорожки нельзя (СП 82 п. 9.22) -- и по
    # предпочтению тоже.
    thorny = next(b for b in BUSHES if b.label.startswith("Барбарис") and assortment_entry(b))
    palette = select_species("path_corridor", "linear_hedge_row", [], BUSHES, _site([thorny.label]))
    assert thorny.label not in [b.label for b in palette.bushes]
    assert "колюч" in preference_note(thorny, ["path_corridor"], _site([thorny.label]))


def test_preference_note_explains_territory_mismatch():
    column = TERRITORY_COLUMN["двор"]
    outside = next(t for t in TREES if assortment_entry(t) and assortment_entry(t).territories.get(column) != "+")
    assert "не рекомендует" in preference_note(outside, ["open_area"], _site())


# --- Благоустройство ----------------------------------------------------------


def test_nothing_is_added_without_improvement_options(scene_02):
    result = plan_improvements(scene_02, BY_ID, GreenPlanOptions())
    assert (result.zones, result.objects, result.notes) == ([], [], [])


def test_new_paths_are_path_zones_without_holes_and_furniture_stays_off_them(scene_02):
    result = plan_improvements(scene_02, BY_ID, ALL_IMPROVEMENTS)
    assert result.zones and all(z.type == "pedestrian_path" and z.name == PATH_LAYER for z in result.zones)
    paths = unary_union([polygon_from_points(z.polygon) for z in result.zones])
    types = {o.type for o in result.objects}
    assert {"lamp", "bench", "trash"} <= types
    for obj in result.objects:
        assert not paths.contains(Point(obj.position.x, obj.position.z))
        assert obj.metadata["generated"] and obj.metadata["source"] == "greenplan"


def test_benches_need_new_paths(scene_02):
    result = plan_improvements(scene_02, BY_ID, GreenPlanOptions(benches=True))
    assert not any(o.type == "bench" for o in result.objects)
    assert any("включите «Дорожки»" in note for note in result.notes)


# --- Запуск целиком -----------------------------------------------------------


def test_plants_respect_new_paths_and_lamps(scene_02):
    run = run_greenplan(scene_02, ALL_IMPROVEMENTS)
    new_ids = {o.id for o in run.new_plants} | {o.id for o in run.improvements.objects}
    assert [v for v in find_violations(run.scene) if v.object_id in new_ids] == []
    paths = unary_union([polygon_from_points(z.polygon) for z in run.scene.restrictions if is_greenplan_zone(z)])
    lamps = [o for o in run.improvements.objects if o.type == "lamp"]
    for plant in run.new_plants:
        assert not paths.contains(Point(plant.position.x, plant.position.z))
        if plant.type == "tree":
            assert all(math.hypot(plant.position.x - lamp.position.x, plant.position.z - lamp.position.z) >= 4.0 - 1e-6 for lamp in lamps)
    rows = {r.species: r for r in improvements_assortment(run.scene)}
    assert rows["Дорожка (новая)"].unit == "м²" and rows["Фонарь"].count == len(lamps)


def test_rerun_replaces_the_previous_result(scene_02):
    first = run_greenplan(scene_02, ALL_IMPROVEMENTS)
    second = run_greenplan(first.scene, ALL_IMPROVEMENTS)
    assert len(second.scene.objects) == len(first.scene.objects)
    assert len(second.scene.restrictions) == len(first.scene.restrictions)
    plain = run_greenplan(first.scene, GreenPlanOptions())
    assert not any(is_greenplan_zone(z) for z in plain.scene.restrictions)
    assert not any(o.type in ("lamp", "bench") and o.metadata.get("source") for o in plain.scene.objects)


def test_planting_toggles(scene_02):
    run = run_greenplan(scene_02, GreenPlanOptions(trees=False, lawn=False))
    assert run.new_plants and all(o.type != "tree" for o in run.new_plants)
    assert all(lawn.status == "existing" for lawn in run.scene.lawns)


def test_lawn_off_keeps_the_drawing_lawn():
    # Газон выключен в параметрах: нового нет, но газон из чертежа остаётся
    # на сцене -- иначе он пропадал бы с экрана после запуска GreenPlan.
    grass = make_zone(id="g", type="lawn", name="GRASS", severity="allowed", polygon=rect_points(-50, -50, 0, 50))
    run = run_greenplan(make_scene(restrictions=[grass]), GreenPlanOptions(lawn=False, trees=False, bushes=False))
    assert run.scene.lawns and {lawn.status for lawn in run.scene.lawns} == {"existing"}


def test_preferred_species_of_a_disabled_category_is_reported(scene_02):
    run = run_greenplan(scene_02, GreenPlanOptions(trees=False, preferred_trees=["species_lipa_melkolistnaya"]))
    assert any("деревья выключены" in note for note in run.notes)


# --- Существующие насаждения с нарушением норм --------------------------------


def _tree(obj_id, x, z, species="Липа мелколистная"):
    return SceneObject(
        id=obj_id, type="tree", model="/models/tree.glb", position=Point3(x=x, y=0.0, z=z),
        rotation=0.0, scale=1.0, metadata={"species": species},
    )


def _with_violating_and_clean_tree(scene):
    """Сцена + дерево внутри здания (нарушение) + дерево на свободной земле без нарушений."""
    inside = polygon_from_points(next(z.polygon for z in scene.restrictions if z.type == "building")).representative_point()
    site = polygon_from_points(scene.boundary.polygon)
    minx, minz, maxx, maxz = site.bounds
    probes = [
        _tree(f"probe_{x}_{z}", x, z)
        for x in range(int(minx), int(maxx), 3)
        for z in range(int(minz), int(maxz), 3)
        if site.contains(Point(x, z))
    ]
    violating = {v.object_id for v in find_violations(scene.model_copy(update={"objects": probes}))}
    clean = next(p for p in probes if p.id not in violating)
    extra = [_tree("tree_bad", inside.x, inside.y), _tree("tree_ok", clean.position.x, clean.position.z)]
    return scene.model_copy(update={"objects": [*scene.objects, *extra]})


def test_remove_violating_plants_removes_only_violators(scene_02):
    scene = _with_violating_and_clean_tree(scene_02)
    kept, removed, notes = remove_violating_plants(scene)
    assert [o.id for o in removed] == ["tree_bad"]
    assert {o.id for o in kept.objects} == {o.id for o in scene.objects} - {"tree_bad"}
    assert "деревьев — 1" in notes[0] and "здания — 1" in notes[0]


def test_remove_violating_plants_reports_when_nothing_to_remove(scene_02):
    kept, removed, notes = remove_violating_plants(scene_02)
    assert kept is scene_02 and removed == []
    assert "удалять нечего" in notes[0]


def test_run_greenplan_removes_violating_plants_only_when_asked(scene_02):
    scene = _with_violating_and_clean_tree(scene_02)
    default = run_greenplan(scene, GreenPlanOptions(lawn=False))
    assert {"tree_bad", "tree_ok"} <= {o.id for o in default.scene.objects} and default.removed_plants == []

    run = run_greenplan(scene, GreenPlanOptions(lawn=False, remove_violating_plants=True))
    ids = {o.id for o in run.scene.objects}
    assert "tree_bad" not in ids and "tree_ok" in ids
    assert [o.id for o in run.removed_plants] == ["tree_bad"]
    assert run.notes[0].startswith("Удалены существующие насаждения с нарушением норм")
    source_ids = {o.id for o in scene.objects}
    assert not [v for v in find_violations(run.scene) if v.object_id in source_ids]


def test_options_summary_mentions_removed_plants():
    rows = GreenPlanOptions(remove_violating_plants=True).summary({})
    assert "существующие деревья и кусты с нарушением норм: удалены" in rows


def test_options_summary_for_the_note():
    options = GreenPlanOptions(style="landscape", lawn=False, preferred_trees=["species_lipa_melkolistnaya"], paths=True)
    rows = options.summary({"species_lipa_melkolistnaya": "Липа мелколистная"})
    assert rows == [
        "стиль участка: пейзажный",
        "посадки: деревья, кустарники",
        "предпочтительные деревья: Липа мелколистная",
        "благоустройство: дорожки",
    ]
