"""pattern_assignment.assign_patterns -- назначение паттернов зонам, GreenPlan
Этап 4 (issue #23). Форма контракта (покрытие всех зон, валидные поля)
проверяется на быстрых синтетических фикстурах location_old/; то, что
retrieval реально находит СЕБЯ и свой задокументированный паттерн (не
подсовывает случайно чужой), можно проверить только на реальном корпусе
locations/ -- см. pattern_corpus.py."""

from pattern_assignment import assign_patterns
from pattern_corpus import _corpus_scenes, load_pattern_log
from pattern_library import PATTERN_LIBRARY
from zone_partitioning import partition_zones


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


def test_site_edge_always_falls_back_to_default():
    """Ни один из 9 проектов retrieval-корпуса не размечает паттерн для
    site_edge в data/pattern_corpus.yaml -- значит для ЛЮБОГО участка эта
    зона обязана получить запасной паттерн (DEFAULT_PATTERN_BY_ZONE_KIND), а
    не выдуманное сходство с одним из соседей."""
    log = load_pattern_log()
    assert all("site_edge" not in zones for zones in log.values())

    scenes = _corpus_scenes()
    scene = next(iter(scenes.values()))
    zones = [z for z in partition_zones(scene) if z.kind == "site_edge"]
    assert zones, "нужна хотя бы одна site_edge зона, чтобы проверить фолбэк"
    for assignment in assign_patterns(scene, zones, k=3):
        assert assignment.confidence == 0.0
        assert assignment.source_project is None


def test_self_retrieval_prefers_own_documented_pattern():
    """У проекта с задокументированным паттерном для zone_kind (например
    12_natashinsky_proezd -> grove_clusters для open_area) сам себе -- самый
    похожий сосед (см. test_pattern_retrieval.test_self_similarity_has_no_leakage),
    поэтому назначение должно указывать на него же, а не на другой проект."""
    slug = "12_natashinsky_proezd"
    scene = _corpus_scenes()[slug]
    zones = [z for z in partition_zones(scene) if z.kind == "open_area"]
    assignments = assign_patterns(scene, zones, k=3)
    assert assignments
    for assignment in assignments:
        assert assignment.pattern_id == "grove_clusters"
        assert assignment.source_project == slug
