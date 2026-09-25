"""greenplan.pattern_assignment.assign_patterns -- назначение паттернов зонам, GreenPlan
Этап 4 (issue #23). Форма контракта (покрытие всех зон, валидные поля)
проверяется на быстрых синтетических фикстурах location_old/; то, что
retrieval реально находит СЕБЯ и свой задокументированный паттерн (не
подсовывает случайно чужой), можно проверить только на реальном корпусе
locations/ -- см. pattern_corpus.py."""

from greenplan import pattern_assignment
from greenplan.pattern_assignment import assign_patterns
from greenplan.pattern_corpus import PatternRecord, _corpus_scenes
from greenplan.pattern_library import DEFAULT_PATTERN_BY_ZONE_KIND, PATTERN_LIBRARY
from greenplan.pattern_retrieval import NeighborMatch
from greenplan.site_characterization import SiteCharacteristics
from greenplan.zone_partitioning import GeometricZone, partition_zones


def test_empty_zones_returns_empty():
    assert assign_patterns(scene=None, zones=[]) == []


def test_covers_every_zone_exactly_once(scene_02):
    zones = partition_zones(scene_02)
    assignments = assign_patterns(scene_02, zones, k=3)
    assert [a.zone_id for a in assignments] == [z.id for z in zones]
    assert len({a.zone_id for a in assignments}) == len(zones)


def test_assignment_fields_are_valid(scene_06):
    zones = partition_zones(scene_06)
    for assignment in assign_patterns(scene_06, zones, k=3):
        assert assignment.pattern_id in PATTERN_LIBRARY
        assert assignment.zone_kind in PATTERN_LIBRARY[assignment.pattern_id].zone_kinds
        assert 0.0 <= assignment.confidence <= 1.0
        # confidence 0 -- запасной паттерн, без выдуманного источника.
        if assignment.confidence == 0.0:
            assert assignment.source_project is None
            assert assignment.source_quote is None
        else:
            assert assignment.source_project is not None
            assert assignment.source_quote is not None


def test_zone_kind_without_any_documented_neighbor_falls_back_to_default():
    """Раньше site_edge вообще не встречался в data/pattern_corpus.yaml (ни
    один проект его не размечал), и этот тест проверял фолбэк именно на нём.
    С 22-24_*_primer (эталоны формы участка) site_edge такую запись
    получил -- сам факт "эта зона документирована хоть где-то в корпусе"
    больше не гарантирует отсутствие покрытия, и тест на конкретном
    zone_kind стал хрупким к росту корпуса. k=0 -- надёжный способ
    воспроизвести тот же код-путь (assign_patterns._fallback) не завися от
    того, что именно уже задокументировано: nearest_projects(..., k=0)
    детерминированно возвращает пустой список соседей (см.
    pattern_retrieval.nearest_projects), значит voting не может найти ни
    одного голоса ни для одной зоны, каким бы ни было содержимое corpus.yaml."""
    scenes = _corpus_scenes()
    scene = next(iter(scenes.values()))
    zones = partition_zones(scene)
    assert zones, "нужна хотя бы одна зона, чтобы проверить фолбэк"
    for assignment in assign_patterns(scene, zones, k=0):
        assert assignment.confidence == 0.0
        assert assignment.source_project is None
        assert assignment.source_quote is None


def test_self_retrieval_prefers_own_documented_pattern():
    """У проекта с задокументированным паттерном для zone_kind (например
    12_natashinsky_proezd -> grove_clusters для open_area) сам себе -- самый
    похожий сосед (см. test_pattern_retrieval.test_self_similarity_has_no_leakage),
    поэтому основное решение -- его собственное: крупнейшая зона и большая
    часть площади. Остальные зоны могут уйти паттернам других похожих
    проектов (зоны делятся пропорционально весу голосов)."""
    slug = "12_natashinsky_proezd"
    scene = _corpus_scenes()[slug]
    zones = [z for z in partition_zones(scene) if z.kind == "open_area"]
    assignments = {a.zone_id: a for a in assign_patterns(scene, zones, k=3)}
    assert assignments
    biggest = max(zones, key=lambda z: z.area_sqm)
    assert assignments[biggest.id].pattern_id == "grove_clusters"
    assert assignments[biggest.id].source_project == slug
    own_area = sum(z.area_sqm for z in zones if assignments[z.id].source_project == slug)
    assert own_area > sum(z.area_sqm for z in zones) / 2


def _open_area(zone_id: str, area: float) -> GeometricZone:
    return GeometricZone(id=zone_id, kind="open_area", polygon=[], area_sqm=area)


def _with_neighbors(monkeypatch, neighbors: list[tuple[str, float, str | None]]) -> None:
    """Подставные соседи: (слаг, сходство, паттерн для open_area или None)."""
    monkeypatch.setattr(
        pattern_assignment, "nearest_projects",
        lambda characteristics, k: [NeighborMatch(slug=s, similarity=sim) for s, sim, _ in neighbors],
    )
    monkeypatch.setattr(
        pattern_assignment, "load_pattern_log",
        lambda: {
            s: {"open_area": PatternRecord(pattern=p, source_quote=f"цитата {s}")} if p else {}
            for s, _, p in neighbors
        },
    )


def _assign(zones: list[GeometricZone], k: int):
    return assign_patterns(None, zones, k=k, characteristics=SiteCharacteristics.model_construct())


def test_neighbor_without_record_for_zone_kind_does_not_take_a_voter_slot(monkeypatch):
    # Ближайший проект open_area не размечал -- раньше он всё равно занимал
    # одно из k мест, и за open_area голосовал единственный оставшийся сосед.
    _with_neighbors(monkeypatch, [
        ("no_open_area", 0.9, None), ("waves", 0.8, "flowing_rows"), ("groves", 0.7, "grove_clusters"),
    ])
    assignments = _assign([_open_area("a", 60.0), _open_area("b", 40.0)], k=2)
    assert {a.source_project for a in assignments} == {"waves", "groves"}


def test_zones_of_one_kind_split_between_patterns_by_vote_weight(monkeypatch):
    _with_neighbors(monkeypatch, [("waves", 0.6, "flowing_rows"), ("groves", 0.4, "grove_clusters")])
    zones = [_open_area("small", 20.0), _open_area("big", 50.0), _open_area("mid", 30.0)]
    assignments = {a.zone_id: a for a in _assign(zones, k=2)}
    # Крупнейшая зона -- самому весомому паттерну, дальше -- кто сильнее
    # недобрал свою долю площади (0.6/0.4): итог 70 м2 волн и 30 м2 рощ.
    assert assignments["big"].pattern_id == "flowing_rows"
    assert assignments["mid"].pattern_id == "grove_clusters"
    assert assignments["small"].pattern_id == "flowing_rows"
    assert assignments["mid"].source_project == "groves"
    assert assignments["big"].confidence == 0.5


def test_single_pattern_among_voters_keeps_every_zone_on_it(monkeypatch):
    _with_neighbors(monkeypatch, [("groves_a", 0.9, "grove_clusters"), ("groves_b", 0.5, "grove_clusters")])
    assignments = _assign([_open_area("a", 10.0), _open_area("b", 90.0)], k=3)
    assert {a.pattern_id for a in assignments} == {"grove_clusters"}
    assert {a.source_project for a in assignments} == {"groves_a"}


def test_neighbors_with_non_positive_similarity_do_not_vote(monkeypatch):
    _with_neighbors(monkeypatch, [("opposite", 0.0, "flowing_rows"), ("far", -0.4, "grove_clusters")])
    (assignment,) = _assign([_open_area("a", 10.0)], k=3)
    assert assignment.confidence == 0.0
    assert assignment.source_project is None


def test_weak_analogy_does_not_vote_and_zone_is_marked_for_review(monkeypatch):
    # Сходство 0.09 -- не аналог: в записке это было бы "по аналогии с
    # проектом X". Без голосов -- запасной паттерн без источника.
    weak = pattern_assignment.MIN_VOTE_SIMILARITY - 0.01
    _with_neighbors(monkeypatch, [("weak", weak, "flowing_rows"), ("weaker", 0.09, "grove_clusters")])
    (assignment,) = _assign([_open_area("a", 10.0)], k=3)
    assert assignment.confidence == 0.0
    assert assignment.source_project is None
    assert assignment.pattern_id == DEFAULT_PATTERN_BY_ZONE_KIND["open_area"]


def test_weak_analogy_is_skipped_but_strong_ones_still_vote(monkeypatch):
    _with_neighbors(monkeypatch, [
        ("strong", 0.8, "grove_clusters"), ("ok", pattern_assignment.MIN_VOTE_SIMILARITY, "grove_clusters"),
        ("weak", 0.1, "flowing_rows"),
    ])
    assignments = _assign([_open_area("a", 60.0), _open_area("b", 40.0)], k=3)
    assert {a.pattern_id for a in assignments} == {"grove_clusters"}
    assert {a.confidence for a in assignments} == {1.0}


# --- Общее решение на участок: стиль, потом приёмы зон ------------------------


def _with_records(monkeypatch, neighbors: list[tuple[str, float, dict[str, str]]]) -> None:
    """Подставные соседи: (слаг, сходство, {вид зоны: паттерн})."""
    monkeypatch.setattr(
        pattern_assignment, "nearest_projects",
        lambda characteristics, k: [NeighborMatch(slug=s, similarity=sim) for s, sim, _ in neighbors],
    )
    monkeypatch.setattr(
        pattern_assignment, "load_pattern_log",
        lambda: {
            s: {kind: PatternRecord(pattern=p, source_quote=f"цитата {s}") for kind, p in records.items()}
            for s, _, records in neighbors
        },
    )


def _zone(zone_id: str, kind: str, area: float = 10.0) -> GeometricZone:
    return GeometricZone(id=zone_id, kind=kind, polygon=[], area_sqm=area)


def test_site_style_is_decided_once_and_other_style_does_not_vote(monkeypatch):
    # Ближайший проект -- регулярный, но двое следующих пейзажные и вместе
    # весомее: стиль участка пейзажный, диагонали в зоны не попадают.
    _with_records(monkeypatch, [
        ("diag", 0.9, {"open_area": "diagonal_rows"}),
        ("waves", 0.8, {"open_area": "flowing_rows"}),
        ("groves", 0.7, {"open_area": "grove_clusters"}),
    ])
    assignments = _assign([_open_area("a", 60.0), _open_area("b", 30.0), _open_area("c", 10.0)], k=3)
    assert {a.site_style for a in assignments} == {"landscape"}
    assert {a.lead_project for a in assignments} == {"waves"}
    assert {a.pattern_id for a in assignments} <= {"flowing_rows", "grove_clusters"}


def test_neutral_patterns_do_not_decide_style_but_stay_allowed(monkeypatch):
    _with_records(monkeypatch, [
        ("hedges", 0.9, {"path_corridor": "linear_hedge_row", "building_border": "building_ring"}),
        ("bosque", 0.6, {"open_area": "formal_bosque_grid"}),
    ])
    zones = [_zone("p", "path_corridor"), _zone("b", "building_border"), _zone("o", "open_area")]
    by_id = {a.zone_id: a for a in _assign(zones, k=3)}
    assert by_id["o"].site_style == "regular" and by_id["o"].lead_project == "bosque"
    assert by_id["p"].pattern_id == "linear_hedge_row" and by_id["p"].source_project == "hedges"
    assert by_id["b"].pattern_id == "building_ring"


def test_style_is_taken_only_from_zone_kinds_present_on_the_site(monkeypatch):
    # У ближайшего проекта регулярные открытые зоны, но на участке их нет --
    # стиль задают те, у кого есть решение для зон, которые есть на участке.
    _with_records(monkeypatch, [
        ("bosque", 0.9, {"open_area": "formal_bosque_grid"}),
        ("wavy_paths", 0.5, {"path_corridor": "flowing_rows"}),
    ])
    (assignment,) = _assign([_zone("p", "path_corridor")], k=3)
    assert assignment.site_style == "landscape"
    assert assignment.pattern_id == "flowing_rows"


def test_zone_without_analogue_in_site_style_gets_the_style_default(monkeypatch):
    # Стиль участка регулярный (его задали диагонали вдоль дорожек);
    # пейзажные рощи в открытую зону не пускаются, регулярного решения для
    # неё у соседей нет -- типовой регулярный приём, а не заливка без замысла.
    _with_records(monkeypatch, [
        ("diag_paths", 0.9, {"path_corridor": "diagonal_rows"}),
        ("groves", 0.5, {"open_area": "grove_clusters"}),
    ])
    by_id = {a.zone_id: a for a in _assign([_zone("p", "path_corridor", 50.0), _zone("o", "open_area")], k=3)}
    assert by_id["p"].site_style == "regular"
    assert by_id["o"].pattern_id == "triangular_grid_fill"
    assert by_id["o"].source_project is None and by_id["o"].confidence == 0.0


def test_generic_fill_votes_only_without_other_decisions(monkeypatch):
    _with_records(monkeypatch, [
        ("plain", 0.9, {"open_area": "generic_fill"}),
        ("groves", 0.6, {"open_area": "grove_clusters"}),
    ])
    assignments = _assign([_open_area("a", 60.0), _open_area("b", 40.0)], k=3)
    assert {a.pattern_id for a in assignments} == {"grove_clusters"}


def test_real_sites_get_a_single_style():
    # Раньше 02_courtyard_3buildings получал на открытых зонах волны,
    # диагональные ряды и заливку одновременно -- от трёх проектов.
    for scene in list(_corpus_scenes().values())[:12]:
        zones = partition_zones(scene)
        assignments = assign_patterns(scene, zones, k=3)
        styles = {PATTERN_LIBRARY[a.pattern_id].style for a in assignments} - {"neutral"}
        assert len(styles) <= 1
        assert len({a.site_style for a in assignments}) <= 1
        if assignments and assignments[0].site_style:
            assert styles <= {assignments[0].site_style}
