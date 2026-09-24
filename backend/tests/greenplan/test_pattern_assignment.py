"""greenplan.pattern_assignment.assign_patterns -- назначение паттернов зонам, GreenPlan
Этап 4 (issue #23). Форма контракта (покрытие всех зон, валидные поля)
проверяется на быстрых синтетических фикстурах location_old/; то, что
retrieval реально находит СЕБЯ и свой задокументированный паттерн (не
подсовывает случайно чужой), можно проверить только на реальном корпусе
locations/ -- см. pattern_corpus.py."""

from greenplan import pattern_assignment
from greenplan.pattern_assignment import assign_patterns
from greenplan.pattern_corpus import PatternRecord, _corpus_scenes
from greenplan.pattern_library import PATTERN_LIBRARY
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
