"""Доводка ИИ-ассистента по итогам оценки на настоящей модели
(tools/ai_editor_eval): каждый тест -- провал, найденный там, воспроизведённый
планом, который модель действительно вернула, и уже без сети."""

import glob
import math
from functools import cache

import pytest
from shapely.geometry import Point, Polygon

from core.plant_catalog import load_catalog
from core.schemas import Scene
from exchange.dxf_parser import parse_dxf_file
from greenplan.violation_report import find_violations
from tests.conftest import ROOT
from text_editor.llm_client import MAX_HISTORY_IDS, _history_block
from text_editor.operations import MAX_TURN_IDS, ChatTurn
from text_editor.plan_common import name_matches
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()


@cache
def _parsed(pattern: str) -> Scene:
    return Scene.model_validate(parse_dxf_file(glob.glob(str(ROOT / "locations" / pattern))[0]))


@pytest.fixture
def play():
    """Эталон 23: детская площадка в кольце дорожек, подъезды, скамейки, урны
    (тип urn), клёны, липы, берёзы и кустарники конкретных видов."""
    return _parsed("23_*/*.dxf").model_copy(deep=True)


def run(scene, *operations):
    return apply_plan(scene, LlmPlan(operations=list(operations), explanation="готово"), CATALOG)


def added(before, result):
    ids = {o.id for o in before.objects}
    return [o for o in result.scene.objects if o.id not in ids]


def count(scene, type_, prefix=None):
    return sum(1 for o in scene.objects if o.type == type_ and (prefix is None or str(o.metadata.get("species", "")).startswith(prefix)))


def playground(scene):
    zone = next(z for z in scene.restrictions if z.type == "playground_zone")
    return Polygon([(p.x, p.z) for p in zone.polygon])


# --- Названия видов -----------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "name", "ok"),
    [
        ("Клен", "Клён мелколистный", True),
        ("клёны", "Клен остролистный", True),
        ("Клен остролистный", "Клен красный", False),
        ("Спирея японская", "Спирея Вангутта", False),
        ("шезлонги", "шезлонг", True),
        ("Туя", "Барбарис Тунберга", False),
    ],
)
def test_name_matches(asked, name, ok):
    assert name_matches(asked, name) is ok


# --- Разбор плана ---------------------------------------------------------------


def test_count_in_a_row_means_max_count(play):
    # "Посади 5 лип вдоль дорожек": модель пишет count -- раньше он молча
    # отбрасывался, и вставало 53 липы.
    result = run(play, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["species_lipa_melkolistnaya"], "count": 5})
    assert len(added(play, result)) == 5


def test_unsupported_field_is_reported_not_silently_dropped(play):
    result = run(play, {"op": "rotate", "id": "bench_001", "rotation_deg": 90, "around": "фонтан"})
    assert any("around" in w for w in result.warnings)


def test_explanation_says_so_when_nothing_was_done(play):
    result = run(play, {"op": "remove_where", "object_types": ["tree"], "species": ["Баобаб"]})
    assert result.explanation.startswith("Не получилось")
    assert run(play, {"op": "remove_where", "object_types": ["urn"]}).explanation == "готово"


# --- Отбор существующих объектов ----------------------------------------------


def test_replace_by_species_touches_only_that_species(play):
    maples, lindens = count(play, "tree", "Клен"), count(play, "tree", "Липа")
    result = run(play, {"op": "replace_where", "object_types": ["tree"], "species": ["Клен"], "catalog_ids": ["species_lipa_melkolistnaya"]})
    assert maples > 0
    assert count(result.scene, "tree", "Клен") == 0
    assert count(result.scene, "tree", "Липа") == lindens + maples


def test_remove_by_ids_removes_exactly_those(play):
    planted = run(play, {"op": "place_in_area", "catalog_ids": ["species_bereza_povislaya"], "count": 4, "x": -18.0, "z": 19.0, "radius_m": 8})
    assert len(planted.added_ids) == 4
    result = run(planted.scene, {"op": "remove_where", "object_types": ["tree"], "ids": planted.added_ids})
    assert {o.id for o in result.scene.objects} == {o.id for o in play.objects}


def test_urns_answer_to_trash(play):
    assert count(play, "urn") > 0
    result = run(play, {"op": "remove_where", "object_types": ["trash"]})
    assert count(result.scene, "urn") == 0


def test_catalog_category_and_russian_type_name_select_objects(play):
    # Модель назвала категорию "furniture" и вид "урна" -- урны, но не скамейки.
    benches = count(play, "bench")
    result = run(play, {"op": "remove_where", "object_types": ["furniture"], "species": ["урны"]})
    assert count(result.scene, "urn") == 0 and count(result.scene, "bench") == benches


def test_near_entrance_edit_widens_to_the_nearest_trees(play):
    # Деревья стоят в 15 м от подъезда 1 (нормы), а модель ищет в 8 м и ещё
    # "у здания" -- правка без удаления берёт ближайшие и пишет об этом.
    entrance = next(o for o in play.objects if o.type == "entrance")
    op = {"op": "resize", "object_types": ["tree"], "target": "building", "distance_m": 5,
          "x": entrance.position.x, "z": entrance.position.z, "radius_m": 8, "scale": 1.3}
    result = run(play, op)
    assert any(o.scale == pytest.approx(1.3) for o in result.scene.objects if o.type == "tree")
    assert any("ничего нет" in w for w in result.warnings)


def test_removal_does_not_widen(play):
    entrance = next(o for o in play.objects if o.type == "entrance")
    result = run(play, {"op": "remove_where", "object_types": ["tree"], "x": entrance.position.x, "z": entrance.position.z, "radius_m": 8})
    assert count(result.scene, "tree") == count(play, "tree")


# --- Посадки у цели -------------------------------------------------------------


def test_group_near_target_stays_by_the_playground(play):
    result = run(play, {"op": "place_in_area", "catalog_ids": ["bench"], "count": 2, "target": "playground", "distance_m": 3})
    benches = added(play, result)
    pg = playground(play)
    assert len(benches) == 2
    assert all(pg.exterior.distance(Point(b.position.x, b.position.z)) <= 12 for b in benches)


def test_enclosure_takes_the_norm_and_steps_past_the_paths(play):
    # Отступ 1 м меньше нормы, а у самого края площадки -- кольцо дорожек.
    result = run(play, {"op": "enclose", "catalog_ids": ["hedge_segment"], "around_target": "playground", "offset_m": 1})
    hedge = added(play, result)
    new_ids = {o.id for o in hedge}
    assert len(hedge) >= 20
    assert not [v for v in find_violations(result.scene) if v.object_id in new_ids]
    assert any("меньше нормы" in w for w in result.warnings)


def test_connect_accepts_an_entrance_id_given_as_target(play):
    entrance = next(o for o in play.objects if o.type == "entrance")
    result = run(play, {"op": "connect", "from_target": entrance.id, "to_target": "playground"})
    assert any(o.type == "path_segment" for o in added(play, result))


def test_fountain_gets_a_plaza_in_a_yard_without_entrances(location_scene):
    # Двор 02: ни одного подъезда -- раньше выбиралась "сетка" без площади,
    # и фонтан молча не вставал.
    scene = location_scene[2].model_copy(deep=True)
    for style in ("auto", "spine", "grid"):
        result = run(scene, {"op": "design_area", "elements": ["paths", "fountain", "benches"], "style": style})
        new = added(scene, result)
        new_ids = {o.id for o in new}
        assert sum(o.type == "fountain" for o in new) == 1, style
        assert not [v for v in find_violations(result.scene) if v.object_id in new_ids], style


# --- История чата ---------------------------------------------------------------


def test_history_lists_the_new_objects_of_a_turn():
    ids = [f"tree_llm_{i:08x}" for i in range(MAX_HISTORY_IDS + 5)]
    block = _history_block([ChatTurn(instruction="посади берёзы", added_ids=ids)])
    assert ids[0] in block and ids[MAX_HISTORY_IDS - 1] in block and ids[MAX_HISTORY_IDS] not in block
    assert "и ещё 5" in block


def test_long_id_list_is_capped_not_rejected():
    # Дизайн двора создаёт сотни объектов -- запрос с их историей не должен
    # падать с 422.
    turn = ChatTurn(instruction="сделай дизайн", added_ids=[f"x{i}" for i in range(MAX_TURN_IDS + 15)])
    assert len(turn.added_ids) == MAX_TURN_IDS


def test_new_objects_are_listed_in_the_result(play):
    result = run(play, {"op": "place_in_area", "catalog_ids": ["species_lipa_melkolistnaya"], "count": 3})
    assert sorted(result.added_ids) == sorted(o.id for o in added(play, result))
    assert math.isclose(len(result.added_ids), 3)
