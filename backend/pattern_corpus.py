"""
Retrieval-корпус GreenPlan, Этап 3 (issue #23). site_characterization.py и
zone_partitioning.py были написаны как задел и намеренно не включали сам
retrieval: "корпус из 20 проектов другой разработчик восстанавливает
отдельно, и пока его нет, сравнивать не с чем" (см. их докстринги). Этот
корпус там до сих пор не появился -- в репозитории нет ни data/projects/, ни
species_catalog.csv, ни decision_codes.csv.

Здесь -- не 20, а 9 проектов: locations/<slug>/ с настоящим DXF и уже
написанным design_rationale.md (прошлая сессия подробно задокументировала,
какой геометрический паттерн применён и почему). Это честный меньший корпус
из данных, которые реально есть в репозитории, а не ожидание второго
разработчика. Если/когда полный корпус появится -- CORPUS_SLUGS и
data/pattern_corpus.yaml расширяются, остальной retrieval-код не меняется.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from schemas import Scene
from site_characterization import SiteCharacteristics, characterize_site
from zone_partitioning import ZoneKind

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

from parse_dxf import parse_dxf_file  # noqa: E402

CORPUS_SLUGS = (
    "02_peschany_pereulok",
    "06_kamchatskaya_ulitsa",
    "07_nizhnie_polya",
    "10_stary_gay",
    "11_frunzenskaya_naberezhnaya",
    "12_natashinsky_proezd",
    "13_kharkovsky_proezd",
    "19_2ya_pryadilnaya",
    "20_makeeva_s",
)

_CORPUS_YAML = ROOT / "data" / "pattern_corpus.yaml"


class PatternRecord(BaseModel):
    pattern: str
    source_quote: str


@lru_cache(maxsize=1)
def load_pattern_log() -> dict[str, dict[ZoneKind, PatternRecord]]:
    """slug -> {вид зоны: запись паттерна}, из data/pattern_corpus.yaml."""
    raw = yaml.safe_load(_CORPUS_YAML.read_text(encoding="utf-8")) or {}
    return {
        slug: {kind: PatternRecord(**record) for kind, record in zones.items()}
        for slug, zones in raw.items()
    }


@lru_cache(maxsize=1)
def _corpus_scenes() -> dict[str, Scene]:
    """slug -> Scene, распарсенный один раз из реального DXF проекта.
    Кэшируется на весь процесс -- 9 файлов, некоторые под мегабайт DXF-текста,
    гонять парсинг на каждый вызов retrieval было бы расточительно."""
    scenes = {}
    for slug in CORPUS_SLUGS:
        dxf_path = ROOT / "locations" / slug / f"{slug}.dxf"
        scenes[slug] = Scene.model_validate(parse_dxf_file(str(dxf_path)))
    return scenes


@lru_cache(maxsize=1)
def corpus_characteristics() -> dict[str, SiteCharacteristics]:
    """slug -> SiteCharacteristics его реального DXF -- вектор для
    pattern_retrieval.nearest_projects()."""
    return {slug: characterize_site(scene) for slug, scene in _corpus_scenes().items()}
