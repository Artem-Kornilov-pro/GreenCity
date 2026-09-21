"""pattern_assignment.assign_patterns -- назначение паттернов зонам, GreenPlan
Этап 4 (issue #23). Форма контракта (покрытие всех зон, валидные поля)
проверяется на быстрых синтетических фикстурах location_old/; то, что
retrieval реально находит СЕБЯ и свой задокументированный паттерн (не
подсовывает случайно чужой), можно проверить только на реальном корпусе
locations/ -- см. pattern_corpus.py."""

from pattern_assignment import assign_patterns
from pattern_corpus import _corpus_scenes
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
    поэтому назначение должно указывать на него же, а не на другой проект."""
    slug = "12_natashinsky_proezd"
    scene = _corpus_scenes()[slug]
    zones = [z for z in partition_zones(scene) if z.kind == "open_area"]
    assignments = assign_patterns(scene, zones, k=3)
    assert assignments
    for assignment in assignments:
        assert assignment.pattern_id == "grove_clusters"
        assert assignment.source_project == slug
