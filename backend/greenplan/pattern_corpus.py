"""
Retrieval-корпус GreenPlan, Этап 3 (issue #23). site_characterization.py и
zone_partitioning.py были написаны как задел и намеренно не включали сам
retrieval: "корпус из 20 проектов другой разработчик восстанавливает
отдельно, и пока его нет, сравнивать не с чем" (см. их докстринги). Этот
корпус там до сих пор не появился -- в репозитории нет ни data/projects/, ни
species_catalog.csv, ни decision_codes.csv.

Здесь -- не 20, а 14 РЕАЛЬНЫХ проектов: locations/<slug>/ с настоящим DXF и
уже написанным design_rationale.md (9 задокументированы в прошлых сессиях,
ещё 5 -- реальные проекты ветки `new-convert`, слитой в main отдельно от
issue #23). Это честный меньший корпус из данных, которые реально есть в
репозитории, а не ожидание второго разработчика. Если/когда полный корпус
появится -- CORPUS_SLUGS и data/pattern_corpus.yaml расширяются, остальной
retrieval-код не меняется.

Плюс отдельная категория -- СИНТЕТИЧЕСКИЕ эталонные участки (`_primer`
в конце слага, например `21_road_buffer_primer`), см.
`green-city-locations-primer-spec.md`: нужны, чтобы retrieval было с чем
сравнивать типы участков (узкий буфер вдоль дороги, маленький сквер и т.п.),
для которых среди 14 реальных проектов пока нет ни одного похожего по
геометрии. У них нет реального заказчика/ПСД -- design_rationale.md у
каждого явно помечен как синтетический, а source_quote в
data/pattern_corpus.yaml цитирует не документ, а обоснование геометрии
самого участка. Смешивать их с реальными в одном списке не проблема:
retrieval сравнивает по геометрии сайта, а не по тому, реальный проект или
нет -- то же соображение, что и с location_old/ (синтетика для юнит-тестов
парсера) не мешает 14 реальным проектам сосуществовать в одном репозитории.
"""

from __future__ import annotations

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
    # new-convert (5 доп. реальных проектов, слиты в main отдельной веткой):
    # тот же принцип честного корпуса из того, что реально есть, а не 20
    # проектов из issue #23.
    "04_kharkovskaya_ulitsa",
    "05_bagritskogo_ulitsa",
    "08_lodochnaya",
    "09_izmaylovskaya_ploshad",
    "17_gruzinskaya_m",
    # Синтетические эталонные участки (green-city-locations-primer-spec.md) --
    # см. докстринг модуля выше про суффикс "_primer".
    "21_road_buffer_primer",
    "22_circular_plaza_primer",
    "23_triangular_plot_primer",
    "24_hexagon_courtyard_primer",
    "25_classical_building_ring_primer",
    "26_diagonal_garden_primer",
    "27_flowing_meadow_primer",
    "28_formal_bosque_primer",
    "29_concentric_rings_primer",
    "30_triangular_grid_primer",
)

_CORPUS_YAML = DATA_DIR / "pattern_corpus.yaml"


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
        dxf_path = LOCATIONS_DIR / slug / f"{slug}.dxf"
        scenes[slug] = Scene.model_validate(parse_dxf_file(str(dxf_path)))
    return scenes


@lru_cache(maxsize=1)
def corpus_characteristics() -> dict[str, SiteCharacteristics]:
    """slug -> SiteCharacteristics его реального DXF -- вектор для
    pattern_retrieval.nearest_projects()."""
    return {slug: characterize_site(scene) for slug, scene in _corpus_scenes().items()}
