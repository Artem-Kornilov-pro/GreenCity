"""
Поиск похожих проектов: k ближайших проектов корпуса по косинусному
сходству вектора признаков участка. Без обучения -- переиспользование
решений, которые архитекторы приняли на похожих участках.

Признаки стандартизуются (z-score) по статистике корпуса, иначе крупные по
величине (логарифм площади) заглушили бы доли. Тип территории входит в
вектор, только если он определён по правилам, а не «неопределено».
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from pydantic import BaseModel

from greenplan.pattern_corpus import CORPUS_SLUGS, corpus_characteristics
from greenplan.site_characterization import SiteCharacteristics

# Фиксированный порядок типов зон в векторе; прочие типы в вектор не входят.
KNOWN_ZONE_TYPES = (
    "building",
    "road",
    "pedestrian_path",
    "gas_pipeline",
    "sewer",
    "water_pipeline",
    "electrical",
    "playground_zone",
)
TERRITORY_TYPES = ("двор", "улица", "площадь", "парк_сквер", "промышленная_охранная", "неопределено")


def _raw_vector(c: SiteCharacteristics) -> np.ndarray:
    total_zones = sum(c.restriction_zone_counts.values()) or 1
    zone_shares = [c.restriction_zone_counts.get(t, 0) / total_zones for t in KNOWN_ZONE_TYPES]
    territory_one_hot = [0.0] * len(TERRITORY_TYPES)
    if not c.territory_type_is_heuristic:
        territory_one_hot[TERRITORY_TYPES.index(c.territory_type)] = 1.0
    return np.array(
        [c.plantable_ratio, math.log1p(c.total_area_sqm), float(c.building_count), *zone_shares, *territory_one_hot]
    )


@lru_cache(maxsize=1)
def _corpus_matrix() -> tuple[tuple[str, ...], np.ndarray, np.ndarray, np.ndarray]:
    """(слаги в фиксированном порядке, сырая матрица корпуса, среднее, стд).
    Среднее/стд считаются один раз по корпусу и используются для
    стандартизации любого вектора -- и корпуса, и нового участка."""
    chars = corpus_characteristics()
    raw = np.array([_raw_vector(chars[slug]) for slug in CORPUS_SLUGS])
    mean = raw.mean(axis=0)
    std = raw.std(axis=0)
    std[std == 0] = 1.0  # признак постоянен по всему корпусу -- не делить на 0
    return CORPUS_SLUGS, raw, mean, std


def _standardize(raw: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (raw - mean) / std


class NeighborMatch(BaseModel):
    slug: str
    similarity: float


def nearest_projects(characteristics: SiteCharacteristics, k: int = 3) -> list[NeighborMatch]:
    """k ближайших проектов корпуса по косинусному сходству, по убыванию."""
    slugs, raw, mean, std = _corpus_matrix()
    corpus_std = _standardize(raw, mean, std)
    query_std = _standardize(_raw_vector(characteristics), mean, std)

    query_norm = np.linalg.norm(query_std) or 1.0
    corpus_norms = np.linalg.norm(corpus_std, axis=1)
    corpus_norms[corpus_norms == 0] = 1.0
    similarities = (corpus_std @ query_std) / (corpus_norms * query_norm)

    order = np.argsort(-similarities)[: min(k, len(slugs))]
    return [NeighborMatch(slug=slugs[i], similarity=float(similarities[i])) for i in order]
