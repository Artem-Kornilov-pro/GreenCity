"""Синтетические эталоны корпуса (tools/primers/build_primers.py): образцы
решений, поэтому -- без единого нарушения норм, с видами растений и
описанием для LLM."""

from collections import Counter

import pytest

from core.paths import LOCATIONS_DIR
from greenplan.pattern_corpus import CORPUS_SLUGS, _corpus_scenes, load_pattern_log
from greenplan.violation_report import find_violations

PRIMERS = [slug for slug in CORPUS_SLUGS if slug.endswith("_primer")]


def test_twenty_primers_half_of_them_streets_with_houses():
    assert len(PRIMERS) == 20
    streets = [slug for slug in PRIMERS if "_street_" in slug]
    assert len(streets) == 10
    for slug in streets:
        scene = _corpus_scenes()[slug]
        assert any(o.type == "building" for o in scene.objects)
        assert any(o.type == "entrance" for o in scene.objects)


@pytest.mark.parametrize("slug", PRIMERS)
def test_primer_is_norm_compliant_and_has_species(slug):
    scene = _corpus_scenes()[slug]
    assert find_violations(scene) == []
    plants = [o for o in scene.objects if o.type in ("tree", "bush")]
    assert plants
    # Вид -- из имени слоя: объект получает позицию каталога и 3D-модель вида.
    assert all(o.metadata.get("species") and o.metadata.get("catalogId") for o in plants)


@pytest.mark.parametrize("slug", PRIMERS)
def test_primer_has_description_and_corpus_record(slug):
    text = (LOCATIONS_DIR / slug / "design_rationale.md").read_text(encoding="utf-8")
    assert text.startswith("# Синтетический эталон — ")
    records = load_pattern_log()[slug]
    assert records and all(len(r.source_quote) > 80 for r in records.values())


def test_stripe_garden_uses_exactly_three_shrub_species():
    scene = _corpus_scenes()["26_diagonal_garden_primer"]
    species = Counter(o.metadata["species"] for o in scene.objects if o.type == "bush")
    assert len(species) == 3
