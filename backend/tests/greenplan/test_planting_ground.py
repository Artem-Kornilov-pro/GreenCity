"""Посадки GreenPlan не встают на тротуар, покрытие и вне земли: ограничение
в расстановке (Placer(planting_ground=True)) и независимая финальная проверка
конвейера (pipeline.keep_on_ground)."""

from shapely.geometry import Point

from core.placement import COMPUTED_GROUND_NAME, curb_lines, curb_setback, ground_area, paved_strips
from core.schemas import Point2
from greenplan.deterministic_placement import plan_site
from greenplan.pipeline import keep_on_ground
from helpers import make_object, make_scene, make_zone


def _square(x0, z0, x1, z1):
    return [Point2(x=x0, z=z0), Point2(x=x1, z=z0), Point2(x=x1, z=z1), Point2(x=x0, z=z1)]


def test_keep_on_ground_drops_plants_off_ground_in_zones_and_at_curbs():
    sidewalk = make_zone(id="p1", type="pedestrian_path", severity="warning", min_distance=0.5, polygon=_square(-40, -2, 40, 2))
    lawn = make_zone(id="a1", type="protected_zone", severity="allowed", min_distance=0.0, polygon=_square(-40, 2, 40, 40))
    scene = make_scene(restrictions=[sidewalk, lawn]).model_copy(update={"curbs": [[Point2(x=-40, z=2), Point2(x=40, z=2)]]})
    plants = [
        make_object("t_on_sidewalk", "tree", 0, 0),
        make_object("t_off_ground", "tree", 0, -20),
        make_object("t_at_curb", "tree", 10, 2.4),
        make_object("b_ok", "bush", 10, 2.6),
        make_object("t_ok_lifted", "tree", 0, 20, y=3.0),
    ]
    kept, notes = keep_on_ground(scene, plants)
    assert [o.id for o in kept] == ["b_ok", "t_ok_lifted"]
    assert all(o.position.y == 0.0 for o in kept)
    assert len(notes) == 1 and "(3)" in notes[0]
    assert "внутри зоны ограничений — 1" in notes[0] and "не на земле — 1" in notes[0] and "у бортового камня — 1" in notes[0]


def test_keep_on_ground_without_problems_keeps_everything_silently():
    plants = [make_object("t1", "tree", 0, 0), make_object("b1", "bush", 5, 5)]
    kept, notes = keep_on_ground(make_scene(), plants)
    assert kept == plants and notes == []


def _with_sidewalk_curbs(scene):
    """Тротуар 3 м, нарисованный одними бортами, -- поперёк всего участка."""
    xs = [p.x for p in scene.boundary.polygon]
    zs = [p.z for p in scene.boundary.polygon]
    mid = (min(zs) + max(zs)) / 2
    curbs = [[Point2(x=min(xs) - 1, z=mid - 1.5), Point2(x=max(xs) + 1, z=mid - 1.5)], [Point2(x=min(xs) - 1, z=mid + 1.5), Point2(x=max(xs) + 1, z=mid + 1.5)]]
    return scene.model_copy(update={"curbs": curbs}), mid


def test_paved_strip_between_curbs_unless_the_drawing_has_lawn_there():
    # Плана газонов нет -- земля вычислена парсером: полоса между бортами -- тротуар.
    ground = make_zone(id="g1", type="protected_zone", name=COMPUTED_GROUND_NAME, severity="allowed", min_distance=0.0, polygon=_square(-50, -50, 50, 50))
    scene, mid = _with_sidewalk_curbs(make_scene(restrictions=[ground]))
    paved = paved_strips(scene)
    assert paved is not None and paved.contains(Point(0, mid)) and not paved.contains(Point(0, mid + 10))
    # Газон в полосе нарисован -- это газонная полоса, сажать можно.
    lawn = make_zone(id="l1", type="protected_zone", name="GRASS", severity="allowed", min_distance=0.0, polygon=_square(-50, mid - 2, 50, mid + 2))
    scene_lawn = scene.model_copy(update={"restrictions": [ground, lawn]})
    assert paved_strips(scene_lawn) is None


def test_plan_site_plants_only_on_ground_and_off_sidewalks(scene_02, catalog):
    # Как у DWG без плана газонов: вся земля вычислена парсером.
    restrictions = [z.model_copy(update={"name": COMPUTED_GROUND_NAME}) if z.severity == "allowed" else z for z in scene_02.restrictions]
    scene, mid = _with_sidewalk_curbs(scene_02.model_copy(update={"restrictions": restrictions}))
    trees = [c for c in catalog if c.category == "tree"]
    bushes = [c for c in catalog if c.category == "bush" and c.object_type == "bush"]
    plan = plan_site(scene, trees, bushes)
    assert plan.objects
    ground, lines = ground_area(scene), curb_lines(scene)
    for obj in plan.objects:
        point = Point(obj.position.x, obj.position.z)
        assert ground is None or ground.contains(point), obj.id
        assert lines.distance(point) >= curb_setback(obj.type), obj.id
        assert not (mid - 1.5 < obj.position.z < mid + 1.5), f"{obj.id} на тротуаре между бортами"
