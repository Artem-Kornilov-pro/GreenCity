"""
Библиотека приёмов озеленения: как приём раскладывается по геометрии зоны.
Виды растений подбирает species_selection.py. Каждый id должен быть и в
data/pattern_corpus.yaml.

geometry_family -- алгоритм расстановки (deterministic_placement.place_zone):
    linear    -- точки с шагом вдоль линии (ось зоны, волна, диагональ, кольцо);
    area_fill -- равномерная заливка площади по решётке;
    clustered -- группы вокруг нескольких центров.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from greenplan.zone_partitioning import ZoneKind

GeometryFamily = Literal["linear", "area_fill", "clustered"]

# Стиль приёма:
#   regular   -- строгая геометрия: боскет, сетка, диагонали, кольца;
#   landscape -- свободные формы: волны, рощи, разброс;
#   neutral   -- уместен в любом стиле (изгородь, кольцо у здания) или
#                заливка без замысла (generic_fill).
PatternStyle = Literal["regular", "landscape", "neutral"]
STYLE_LABELS: dict[str, str] = {"regular": "регулярный", "landscape": "пейзажный"}


class PatternSpec(BaseModel):
    id: str
    label: str
    geometry_family: GeometryFamily
    # Виды зон, где приём уместен.
    zone_kinds: frozenset[ZoneKind]
    style: PatternStyle = "neutral"
    # geometry_family == "linear": вторая линия в double_row_offset_m от
    # первой (0.0 -- однорядно).
    double_row_offset_m: float = 0.0
    # geometry_family == "clustered": размер одной группы (мин, макс).
    group_size: tuple[int, int] = (2, 4)
    # Форма линии для linear:
    #   straight   -- прямая вдоль длинной оси зоны;
    #   wavy       -- та же прямая, изогнутая синусом;
    #   diagonal   -- прямая под углом diagonal_angle_deg к оси;
    #   ring       -- контур зоны (кольцо вокруг здания);
    #   concentric -- вложенные кольца вокруг центра открытой площади.
    line_shape: Literal["straight", "wavy", "diagonal", "ring", "concentric"] = "straight"
    # line_shape == "wavy": амплитуда и длина волны синус-модуляции, по
    # факту из 10_stary_gay (data/pattern_corpus.yaml, "flowing_rows").
    wave_amplitude_m: float = 0.0
    wave_length_m: float = 1.0  # не 0, чтобы не делить на ноль, если amplitude=0 (волна не применяется)
    # line_shape == "diagonal": угол между линией и длинной осью зоны.
    diagonal_angle_deg: float = 0.0
    # linear: только деревья, без кустарника (боскет -- сетка стволов, не изгородь).
    trees_only: bool = False
    # linear: шаг деревьев вдоль линии, если нужен свой (квадратная сетка боскета).
    tree_step_m: Optional[float] = None
    # line_shape == "concentric": шаг радиуса между соседними кольцами.
    ring_spacing_m: float = 6.0
    # area_fill: брать точки прямо с решётки, без прореживания -- для видимого
    # регулярного узора (треугольная сетка).
    dense_lattice: bool = False


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
        line_shape="ring",
    ),
    "diagonal_rows": PatternSpec(
        id="diagonal_rows",
        style="regular",
        label="Диагональные линии под углом к проезжей части",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area", "path_corridor"}),
        line_shape="diagonal",
        diagonal_angle_deg=45.0,  # "под углом к проезжей части" -- 45° как нейтральный диагональный угол без данных о реальной ориентации дороги
    ),
    "flowing_rows": PatternSpec(
        id="flowing_rows",
        style="landscape",
        label="Волнистые (синус-модулированные) линии",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area", "path_corridor"}),
        double_row_offset_m=1.2,  # как в 10_stary_gay: вторая волна в 1.2 м от первой
        line_shape="wavy",
        wave_amplitude_m=4.0,  # 10_stary_gay (data/pattern_corpus.yaml): амплитуда 4 м
        wave_length_m=40.0,  # 10_stary_gay: длина волны 40 м
    ),
    "formal_bosque_grid": PatternSpec(
        id="formal_bosque_grid",
        style="regular",
        label="Формальный боскет -- строгая ортогональная сетка деревьев",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area"}),
        line_shape="straight",
        trees_only=True,
        # Квадратная сетка: шаг как между рядами (OPEN_AREA_ROW_SPACING_M);
        # числом, а не импортом -- иначе цикл импорта.
        tree_step_m=6.0,
    ),
    "concentric_rings": PatternSpec(
        id="concentric_rings",
        style="regular",
        label="Концентрические кольца вокруг центра площадки",
        geometry_family="linear",
        zone_kinds=frozenset({"open_area"}),
        line_shape="concentric",
        ring_spacing_m=6.0,
    ),
    "triangular_grid_fill": PatternSpec(
        id="triangular_grid_fill",
        style="regular",
        label="Треугольная (гексагональная) сетка -- квинкункс",
        geometry_family="area_fill",
        zone_kinds=frozenset({"open_area"}),
        dense_lattice=True,
    ),
    "grove_clusters": PatternSpec(
        id="grove_clusters",
        style="landscape",
        label="Органичные рощи -- случайные группы по несколько деревьев",
        geometry_family="clustered",
        zone_kinds=frozenset({"open_area"}),
        group_size=(2, 5),  # 12/13/19 документируют группы от 2-4 до 3-7 деревьев
    ),
    "poisson_scatter_fill": PatternSpec(
        id="poisson_scatter_fill",
        style="landscape",
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

# Типовой приём для вида зоны без аналогов.
DEFAULT_PATTERN_BY_ZONE_KIND: dict[ZoneKind, str] = {
    "building_border": "building_ring",
    "path_corridor": "linear_hedge_row",
    "site_edge": "linear_hedge_row",
    "open_area": "generic_fill",
}


# Типовой приём по стилю участка; видов зон нет в словаре -- по
# DEFAULT_PATTERN_BY_ZONE_KIND.
DEFAULT_PATTERN_BY_STYLE: dict[str, dict[ZoneKind, str]] = {
    "regular": {"open_area": "triangular_grid_fill"},
    "landscape": {"open_area": "grove_clusters"},
}


def default_pattern(zone_kind: ZoneKind, site_style: Optional[str]) -> str:
    return DEFAULT_PATTERN_BY_STYLE.get(site_style or "", {}).get(zone_kind) or DEFAULT_PATTERN_BY_ZONE_KIND[zone_kind]


def pattern_spec(pattern_id: str) -> PatternSpec:
    return PATTERN_LIBRARY[pattern_id]
