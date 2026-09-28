"""greenplan.pattern_retrieval.nearest_projects -- retrieval для Этапа 3 GreenPlan
(issue #23). В отличие от site_characterization/zone_partitioning, здесь
синтетические фикстуры location_old/ не годятся: сам предмет теста -- то,
насколько retrieval-корпус (9 реальных проектов locations/, см.
pattern_corpus.py) внутренне непротиворечив, а это можно проверить только на
нём самом. Парсинг всех 9 DXF кэшируется в pattern_corpus.py (lru_cache) --
цена платится один раз за весь прогон тестов, а не в каждом тесте отдельно.
"""

import json

import numpy as np
import pytest

from greenplan import pattern_corpus
from greenplan.pattern_corpus import CORPUS_SLUGS, corpus_characteristics
from greenplan.pattern_retrieval import TERRITORY_TYPES, _raw_vector, nearest_projects
from greenplan.site_characterization import SiteCharacteristics


def test_self_similarity_has_no_leakage():
    """Каждый проект корпуса при поиске похожих на самого себя должен найти
    самого себя первым с максимальным сходством -- иначе стандартизация или
    косинусное расстояние считаются неправильно (например, вектор
    какого-то проекта случайно совпадает с другим при разных исходных
    характеристиках)."""
    chars = corpus_characteristics()
    for slug in CORPUS_SLUGS:
        neighbors = nearest_projects(chars[slug], k=3)
        assert neighbors[0].slug == slug
        assert neighbors[0].similarity == pytest.approx(1.0)


def test_k_is_respected_and_sorted_descending():
    chars = corpus_characteristics()
    neighbors = nearest_projects(chars[CORPUS_SLUGS[0]], k=4)
    assert len(neighbors) == 4
    similarities = [n.similarity for n in neighbors]
    assert similarities == sorted(similarities, reverse=True)


def test_k_larger_than_corpus_is_clamped():
    chars = corpus_characteristics()
    neighbors = nearest_projects(chars[CORPUS_SLUGS[0]], k=1000)
    assert len(neighbors) == len(CORPUS_SLUGS)


def test_raw_vector_ignores_heuristic_territory_type():
    """territory_type_is_heuristic=True (сейчас -- всегда, см.
    site_characterization.py) должно занулять one-hot часть вектора --
    иначе непроверенная догадка получила бы такой же вес, как измеренная
    площадь/состав зон."""
    base = dict(
        total_area_sqm=1000.0,
        plantable_area_sqm=500.0,
        plantable_ratio=0.5,
        building_count=1,
        restriction_zone_counts={},
        existing_tree_species={},
        territory_type="двор",
    )
    heuristic = SiteCharacteristics(**base, territory_type_is_heuristic=True)
    confirmed = SiteCharacteristics(**base, territory_type_is_heuristic=False)

    vec_heuristic = _raw_vector(heuristic)
    vec_confirmed = _raw_vector(confirmed)

    territory_slice = slice(-len(TERRITORY_TYPES), None)
    assert np.all(vec_heuristic[territory_slice] == 0.0)
    assert vec_confirmed[territory_slice].sum() == 1.0
    # Остальная часть вектора (числовые признаки) не должна зависеть от флага.
    assert np.array_equal(vec_heuristic[: territory_slice.start], vec_confirmed[: territory_slice.start])


# --- Признаки корпуса из data/pattern_corpus_features.json --------------------


def test_features_file_is_up_to_date():
    # Файл устарел (изменился DXF проекта или код characterize_site) --
    # обновить: make corpus-features
    stored = json.loads(pattern_corpus.FEATURES_JSON.read_text(encoding="utf-8"))
    assert stored == pattern_corpus.compute_features()


def test_dxf_hash_ignores_line_endings(tmp_path, monkeypatch):
    # Windows-checkout (core.autocrlf) отдаёт те же DXF с CRLF -- признаки
    # корпуса из-за этого не должны считаться устаревшими.
    monkeypatch.setattr(pattern_corpus, "LOCATIONS_DIR", tmp_path)
    (tmp_path / "p").mkdir()
    dxf = tmp_path / "p" / "p.dxf"
    dxf.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    lf = pattern_corpus._dxf_sha256("p")
    dxf.write_bytes(dxf.read_bytes().replace(b"\n", b"\r\n"))
    assert pattern_corpus._dxf_sha256("p") == lf


def test_stale_project_is_recomputed_from_dxf(tmp_path, monkeypatch, caplog):
    stored = json.loads(pattern_corpus.FEATURES_JSON.read_text(encoding="utf-8"))
    slug = "25_classical_building_ring_primer"
    expected = stored[slug]["characteristics"]
    stored[slug] = {"dxf_sha256": "0" * 64, "characteristics": {**expected, "building_count": 999}}
    stale = tmp_path / "features.json"
    stale.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(pattern_corpus, "FEATURES_JSON", stale)
    pattern_corpus.corpus_characteristics.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="greencity.greenplan"):
            chars = pattern_corpus.corpus_characteristics()
        assert chars[slug].model_dump(mode="json") == expected
        assert slug in caplog.text
    finally:
        pattern_corpus.corpus_characteristics.cache_clear()
