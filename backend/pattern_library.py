"""
Словарь геометрических СЕМЕЙСТВ паттернов озеленения -- задел под Этап 4
GreenPlan (issue #23, "Назначение зон/паттернов"). Записи здесь описывают,
КАК паттерн раскладывается по геометрии зоны (zone_partitioning.GeometricZone),
а не какие виды растений использовать -- ассортимент остаётся в
plant_catalog.py и выбирается вызывающим кодом отдельно.

Каждый id соответствует записи в data/pattern_corpus.yaml (pattern_corpus.py
её загружает и связывает с реальными проектами retrieval-корпуса) -- если
здесь появляется новый id, его нужно завести и там, иначе retrieval не
сможет на него сослаться.

geometry_family определяет, какой алгоритм расстановки применяет
deterministic_placement.place_zone:
    linear   -- точки вдоль длинной оси зоны с шагом (Placer.points_along-
                подобная логика на centerline_of(), см. placement.py).
    area_fill -- кандидаты на гексагональной сетке по всей площади зоны
                (Placer.points_in_area) + равномерный отбор (pick_spread).
    clustered -- несколько случайных центров внутри зоны + компактная
                группа вокруг каждого (Placer.pick_near).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from zone_partitioning import ZoneKind

GeometryFamily = Literal["linear", "area_fill", "clustered"]


class PatternSpec(BaseModel):
    id: str
    label: str
    geometry_family: GeometryFamily
    # Зоны, где паттерн семантически уместен -- используется как проверка
    # при назначении (pattern_assignment.py не назначит building_ring зоне
    # типа open_area, даже если в корпусе почему-то нашлась бы такая запись).
    zone_kinds: frozenset[ZoneKind]
    # geometry_family == "linear": вторая линия в double_row_offset_m от
    # первой (0.0 -- однорядно). Единственная геометрическая развилка внутри
    # ОДНОГО параметризуемого линейного алгоритма (issue #23, Этап 5: "один
    # параметризуемый алгоритм смещения вдоль линии, переиспользуемый для
    # всех линейных типов") -- diagonal_rows/flowing_rows не получают
    # отдельного кода под угол/синус-волну, только тот же алгоритм вдоль
    # собственной длинной оси зоны (centerline_of в placement.py); точное
    # визуальное отличие (диагональ под углом к дороге, "текучая" волна),
    # которое прошлая сессия рисовала одноразовыми скриптами, здесь не
    # воспроизводится -- честный компромисс за один переиспользуемый алгоритм.
    double_row_offset_m: float = 0.0
    # geometry_family == "clustered": размер одной группы (мин, макс).
    group_size: tuple[int, int] = (2, 4)


PATTERN_LIBRARY: dict[str, PatternSpec] = {
    "linear_hedge_row": PatternSpec(
        id="linear_hedge_row",
        label="Линейная посадка (живая изгородь/ряд вдоль полосы)",
        geometry_family="linear",
        zone_kinds=frozenset({"building_border", "path_corridor", "site_edge"}),
    ),
    "building_ring": PatternSpec(
        id="building_ring",
        label="Кольцо кустарника вокруг здания",
        geometry_family="linear",
        zone_kinds=frozenset({"building_border"}),
    ),
    "diagonal_rows": PatternSpec(
        id="diagonal_rows",
        label="Диагональные линии под углом к проезжей части",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area", "path_corridor"}),
    ),
    "flowing_rows": PatternSpec(
        id="flowing_rows",
        label="Волнистые (синус-модулированные) линии",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area", "path_corridor"}),
        double_row_offset_m=1.2,  # как в 10_stary_gay: вторая волна в 1.2 м от первой
    ),
    "grove_clusters": PatternSpec(
        id="grove_clusters",
        label="Органичные рощи -- случайные группы по несколько деревьев",
        geometry_family="clustered",
        zone_kinds=frozenset({"open_area"}),
        group_size=(2, 5),  # 12/13/19 документируют группы от 2-4 до 3-7 деревьев
    ),
    "poisson_scatter_fill": PatternSpec(
        id="poisson_scatter_fill",
        label="Равномерный редкий разброс по всей площади (без кластеров)",
        geometry_family="area_fill",
        zone_kinds=frozenset({"open_area"}),
    ),
    "generic_fill": PatternSpec(
        id="generic_fill",
        label="Обычная равномерная заливка (нет задокументированной концепции)",
        geometry_family="area_fill",
        zone_kinds=frozenset({"building_border", "path_corridor", "site_edge", "open_area"}),
    ),
}

# Паттерн по умолчанию для вида зоны, если НИ ОДИН retrieval-сосед не
# использовал этот zone_kind ни разу (pattern_assignment.py помечает такое
# назначение confidence=0.0, source_project=None -- честно видно, что это
# запасной вариант, а не результат поиска похожих проектов).
DEFAULT_PATTERN_BY_ZONE_KIND: dict[ZoneKind, str] = {
    "building_border": "building_ring",
    "path_corridor": "linear_hedge_row",
    "site_edge": "linear_hedge_row",
    "open_area": "generic_fill",
}


def pattern_spec(pattern_id: str) -> PatternSpec:
    return PATTERN_LIBRARY[pattern_id]
