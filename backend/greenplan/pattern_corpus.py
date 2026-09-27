"""
Корпус проектов-аналогов GreenPlan: 14 реальных проектов (locations/<slug>/
с DXF и design_rationale.md -- обоснованием решений из пояснительной записки)
и 20 синтетических эталонов (слаг с суффиксом _primer, генератор
tools/primers/): типовые случаи, которых среди реальных нет или мало.
Поиск сравнивает участки по геометрии, а не по тому, реальный проект или нет.

Решения проектов по видам зон -- data/pattern_corpus.yaml.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

import yaml
from pydantic import BaseModel

from core.paths import DATA_DIR, LOCATIONS_DIR
from core.schemas import Scene
from exchange.dxf_parser import parse_dxf_file
from greenplan.site_characterization import SiteCharacteristics, characterize_site
from greenplan.zone_partitioning import ZoneKind

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
    "04_kharkovskaya_ulitsa",
    "05_bagritskogo_ulitsa",
    "08_lodochnaya",
    "09_izmaylovskaya_ploshad",
    "17_gruzinskaya_m",
    # Синтетические эталонные участки (tools/primers/build_primers.py) --
    # см. докстринг модуля выше про суффикс "_primer".
    "21_road_buffer_primer",
    "22_circular_plaza_primer",
    "23_playground_yard_primer",
    "24_boulevard_primer",
    "25_classical_building_ring_primer",
    "26_diagonal_garden_primer",
    "27_flowing_meadow_primer",
    "28_formal_bosque_primer",
    "29_green_parking_primer",
    "30_network_corridor_garden_primer",
    # Фрагменты улиц с домами и подъездами (tools/primers/street_primers.py).
    "31_street_front_gardens_primer",
    "32_street_shops_primer",
    "33_street_corner_primer",
    "34_street_slab_yard_primer",
    "35_street_townhouses_primer",
    "36_street_clinic_primer",
    "37_street_long_house_primer",
    "38_street_median_primer",
    "39_street_bike_lane_primer",
    "40_street_cul_de_sac_primer",
)

_CORPUS_YAML = DATA_DIR / "pattern_corpus.yaml"
# Признаки участков корпуса посчитаны заранее (python -m greenplan.pattern_corpus)
# вместе с sha256 исходного DXF: изменился файл -- признаки пересчитываются.
FEATURES_JSON = DATA_DIR / "pattern_corpus_features.json"

log = logging.getLogger("greencity.greenplan")


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
        scenes[slug] = Scene.model_validate(parse_dxf_file(str(_dxf_path(slug))))
    return scenes


def _dxf_path(slug: str):
    return LOCATIONS_DIR / slug / f"{slug}.dxf"


def _dxf_sha256(slug: str) -> str:
    return hashlib.sha256(_dxf_path(slug).read_bytes()).hexdigest()


def compute_features() -> dict[str, dict]:
    """Содержимое FEATURES_JSON: признаки каждого проекта корпуса по его DXF."""
    return {
        slug: {"dxf_sha256": _dxf_sha256(slug), "characteristics": characterize_site(scene).model_dump(mode="json")}
        for slug, scene in _corpus_scenes().items()
    }


def write_features() -> None:
    FEATURES_JSON.write_text(json.dumps(compute_features(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


@lru_cache(maxsize=1)
def corpus_characteristics() -> dict[str, SiteCharacteristics]:
    """slug -> SiteCharacteristics его реального DXF -- вектор для
    pattern_retrieval.nearest_projects(). Из FEATURES_JSON; проект, которого
    там нет или чей DXF изменился, считается по DXF (и об этом пишется в лог:
    файл признаков пора обновить)."""
    stored = json.loads(FEATURES_JSON.read_text(encoding="utf-8")) if FEATURES_JSON.exists() else {}
    result = {}
    for slug in CORPUS_SLUGS:
        entry = stored.get(slug)
        if entry and entry["dxf_sha256"] == _dxf_sha256(slug):
            result[slug] = SiteCharacteristics.model_validate(entry["characteristics"])
        else:
            log.warning("признаки проекта корпуса %s устарели -- считаю по DXF; обновите: make corpus-features", slug)
            result[slug] = characterize_site(Scene.model_validate(parse_dxf_file(str(_dxf_path(slug)))))
    return result


if __name__ == "__main__":
    write_features()
    print(f"{FEATURES_JSON}: {len(CORPUS_SLUGS)} проектов")
