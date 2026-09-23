"""
Естественный вид посадки генератора по сетке (greenery_generator.generate_trees):
органичный разброс Poisson-disk вместо регулярной сетки, пятна гуще/реже
посадки (шум плотности) и смесь видов. Всё детерминировано: фиксированный сид
и хеш координат -- один и тот же участок даёт один и тот же результат.
"""

from __future__ import annotations

import hashlib
import math
import random

from core.setback_norms import DEFAULT_TREE_SPECIES

# --- Естественность посадки: органичный разброс + переменная плотность + ---
# --- смесь видов (вместо регулярной сетки, одного вида и ровного ковра) ----
#
# Раньше кандидаты деревьев шли строго по узлам регулярной сетки с шагом
# grid_spacing -- на глаз это стабильно читалось как ряды/решётка, даже после
# прореживания по min_spacing (прореживание убирает лишние точки, но не
# трогает то, что оставшиеся всё равно лежат на пересечениях сетки).
# _poisson_disk_sample ниже даёт органичный ("blue noise") разброс с той же
# гарантией минимального расстояния между соседями, но без выравнивания по
# осям x/z. grid_spacing/min_tree_spacing_m сохраняют прежний смысл: первый
# управляет плотностью кандидатов (теперь -- радиусом Poisson-disk), второй --
# по-прежнему жёсткая гарантия минимального расстояния между стволами,
# обеспечивается тем же _SpacingGrid greedy-отбором, что и раньше.

# Фиксированный сид детерминированного ГСЧ Poisson-disk -- тот же принцип, что
# и у остального генератора (докстринг модуля: "deterministic demo generator",
# без случайности, чтобы результат был воспроизводим). Один и тот же Scene с
# одними и теми же параметрами всегда даёт один и тот же список точек.
_POISSON_SEED = 20260921
_POISSON_MAX_ATTEMPTS = 30
# Радиус Poisson-disk не может быть 0 (деление на 0 в размере фоновой сетки
# алгоритма) -- нижний пол чуть ниже MIN_ALLOWED_GRID_SPACING_M/2, не влияет
# на реальные вызовы (grid_spacing уже зажат сверху этим минимумом раньше).
_POISSON_MIN_RADIUS_M = 0.2

# Смешение видов, когда species НЕ передан явно вызывающим кодом (пользователь
# не выбрал конкретный вид в UI) -- реальные посадки почти никогда
# монокультурны. Виды и веса не выдуманы: все трое уже фигурируют как виды
# ДЕРЕВЬЕВ в реальных документированных проектах retrieval-корпуса GreenPlan
# (data/pattern_corpus.yaml) -- "Липа мелколистная" (02_peschany_pereulok,
# и она же DEFAULT_TREE_SPECIES), "Клён остролистный" (07_nizhnie_polya),
# "Берёза полезная" (20_makeeva_s). Отступы у них разные (липа и клён --
# широкая крона, 10 м от здания по 743-ПП, см. setback_norms.py), а
# _keep_out_shapes считается один раз на весь вызов, а не на каждую точку --
# поэтому для смеси берётся самый строгий отступ среди её видов (см.
# setback_species ниже): берёза тоже встанет не ближе 10 м к дому, зато ни
# одна липа не окажется ближе нормы.
DEFAULT_TREE_SPECIES_MIX: list[tuple[str, float]] = [
    (DEFAULT_TREE_SPECIES, 0.6),
    ("Клён остролистный", 0.25),
    ("Берёза полезная", 0.15),
]

# Шум плотности (см. _value_noise/_keep_probability): характерный размер
# одного "пятна" гуще/реже посадки. Не привязан к grid_spacing -- это
# отдельный, более крупный масштаб неоднородности, а не шаг между деревьями.
_DENSITY_NOISE_CELL_M = 12.0
# Нижняя граница вероятности сохранить точку в самом "разреженном" пятне -- не
# 0, иначе в шумном минимуме получались бы настоящие проплешины без единого
# дерева, а не естественная неровность плотности.
_DENSITY_MIN_KEEP_PROBABILITY = 0.35


def _hash01(*parts: float, salt: str) -> float:
    """Детерминированное псевдослучайное число в [0, 1) -- ЧИСТАЯ функция
    переданных координат/аргументов, не зависящая от порядка вызовов (в
    отличие от random.Random, которым нельзя пользоваться здесь: шум
    плотности и выбор вида должны давать один и тот же результат для одной и
    той же точки независимо от того, в каком порядке Poisson-disk её посетил
    и сколько раз к ней обращались)."""
    key = salt + ":" + ":".join(f"{p:.6f}" for p in parts)
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _value_noise(x: float, z: float, cell: float) -> float:
    """Билинейно интерполированный value noise по хешированной решётке узлов
    -- простое, без внешних зависимостей приближение шумового поля,
    достаточное для "пятен" плотности демо-генератора. Чистая функция (x, z):
    то же значение при повторном вызове с теми же координатами, каким бы
    путём Poisson-disk до них ни дошёл."""
    gx, gz = x / cell, z / cell
    x0, z0 = math.floor(gx), math.floor(gz)
    tx, tz = gx - x0, gz - z0

    def corner(ix: float, iz: float) -> float:
        return _hash01(ix, iz, salt="density-noise")

    top = corner(x0, z0) * (1 - tx) + corner(x0 + 1, z0) * tx
    bottom = corner(x0, z0 + 1) * (1 - tx) + corner(x0 + 1, z0 + 1) * tx
    return top * (1 - tz) + bottom * tz


def _keep_probability(x: float, z: float) -> float:
    """Вероятность сохранить кандидатную точку (x, z) при прореживании по
    плотности -- 1.0 в самых "густых" пятнах шумового поля, снижается до
    _DENSITY_MIN_KEEP_PROBABILITY в самых "разреженных". Прореживание -- это
    ТОЛЬКО удаление точек из уже готового Poisson-disk разброса, поэтому
    гарантия минимального расстояния между оставшимися точками не портится
    (уменьшить расстояние между двумя точками удаление не может)."""
    noise = _value_noise(x, z, _DENSITY_NOISE_CELL_M)
    return _DENSITY_MIN_KEEP_PROBABILITY + noise * (1.0 - _DENSITY_MIN_KEEP_PROBABILITY)


def _pick_species(x: float, z: float, mix: list[tuple[str, float]]) -> str:
    """Детерминированный выбор вида для точки (x, z) из взвешенного списка --
    чистая функция координат (тот же вид при повторном вызове с теми же
    координатами), веса нормализуются на случай, если не суммируются в 1.0."""
    roll = _hash01(x, z, salt="species-mix")
    total = sum(weight for _, weight in mix) or 1.0
    acc = 0.0
    for species, weight in mix:
        acc += weight / total
        if roll < acc:
            return species
    return mix[-1][0]


def _poisson_disk_sample(
    min_x: float, min_z: float, max_x: float, max_z: float, radius: float, rng: random.Random
) -> list[tuple[float, float]]:
    """Bridson Poisson-disk sampling ("Fast Poisson Disk Sampling in
    Arbitrary Dimensions", 2007) -- органичный, но при этом равномерный
    разброс точек с гарантированным минимальным расстоянием radius друг от
    друга, без видимой сетки (в отличие от прежнего подхода "регулярная
    сетка + жадный отбор по расстоянию"). rng -- ОДИН random.Random с
    фиксированным сидом (_POISSON_SEED) на весь вызов generate_trees:
    детерминированная последовательность операций с тем же сидом всегда даёт
    тот же результат для одной и той же геометрии участка."""
    radius = max(radius, _POISSON_MIN_RADIUS_M)
    if max_x <= min_x or max_z <= min_z:
        return []

    cell = radius / math.sqrt(2)
    grid: dict[tuple[int, int], tuple[float, float]] = {}

    def grid_index(x: float, z: float) -> tuple[int, int]:
        return (int((x - min_x) / cell), int((z - min_z) / cell))

    def fits(x: float, z: float) -> bool:
        gx, gz = grid_index(x, z)
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                neighbor = grid.get((gx + dx, gz + dz))
                if neighbor is not None and math.hypot(neighbor[0] - x, neighbor[1] - z) < radius:
                    return False
        return True

    first = (rng.uniform(min_x, max_x), rng.uniform(min_z, max_z))
    samples = [first]
    grid[grid_index(*first)] = first
    active = [first]

    while active:
        idx = rng.randrange(len(active))
        origin = active[idx]
        placed = False
        for _ in range(_POISSON_MAX_ATTEMPTS):
            angle = rng.uniform(0, 2 * math.pi)
            dist = rng.uniform(radius, 2 * radius)
            x = origin[0] + dist * math.cos(angle)
            z = origin[1] + dist * math.sin(angle)
            if not (min_x <= x <= max_x and min_z <= z <= max_z):
                continue
            if not fits(x, z):
                continue
            point = (x, z)
            samples.append(point)
            grid[grid_index(x, z)] = point
            active.append(point)
            placed = True
            break
        if not placed:
            active.pop(idx)

    return samples


class _SpacingGrid:
    """Пространственная хеш-сетка для проверки min_spacing между уже
    выбранными точками при обходе сетки кандидатов -- замена линейному
    перебору `any(... for sx, sz in selected)`. На плотных реальных данных
    (большой участок, тысячи кандидатов и тысячи уже выбранных деревьев --
    без явных зон газона в сцене кандидаты идут по всей площади участка, а
    не только по узким полосам вдоль дорожек) линейный перебор давал
    квадратичный рост и был главным узким местом generate-greenery (не
    сами shapely-операции над зонами ограничений -- те быстрые и без этой
    правки, см. историю правок). Ячейка размером с min_spacing -- тогда все
    точки, которые могут оказаться ближе min_spacing, лежат в одной из 9
    соседних ячеек (текущая + 8 вокруг)."""

    def __init__(self, min_spacing: float):
        self._cell = max(min_spacing, 1e-6)
        self._buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}

    def _key(self, x: float, z: float) -> tuple[int, int]:
        return (int(x // self._cell), int(z // self._cell))

    def is_far_enough(self, x: float, z: float, min_spacing: float) -> bool:
        cx, cz = self._key(x, z)
        min_sq = min_spacing * min_spacing
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for sx, sz in self._buckets.get((cx + dx, cz + dz), ()):
                    if (x - sx) ** 2 + (z - sz) ** 2 < min_sq:
                        return False
        return True

    def add(self, x: float, z: float) -> None:
        self._buckets.setdefault(self._key(x, z), []).append((x, z))
