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

# Кандидаты деревьев -- разброс Poisson-disk вместо регулярной сетки: на глаз
# сетка читалась рядами даже после прореживания. grid_spacing задаёт радиус
# разброса, min_tree_spacing_m по-прежнему гарантирует расстояние между стволами.

# Фиксированный сид: одна и та же сцена с теми же параметрами даёт те же точки.
_POISSON_SEED = 20260921
_POISSON_MAX_ATTEMPTS = 30
# Радиус Poisson-disk не может быть 0 (деление на размер ячейки).
_POISSON_MIN_RADIUS_M = 0.2

# Смесь видов, если вид не выбран: реальные посадки редко монокультурны. Виды
# взяты из проектов корпуса GreenPlan. Отступ для смеси -- самый строгий из
# её видов (липа и клён -- 10 м от здания по 743-ПП), поэтому ни один вид не
# окажется ближе нормы.
DEFAULT_TREE_SPECIES_MIX: list[tuple[str, float]] = [
    (DEFAULT_TREE_SPECIES, 0.6),
    ("Клён остролистный", 0.25),
    ("Берёза полезная", 0.15),
]

# Характерный размер пятна гуще/реже посадки, м.
_DENSITY_NOISE_CELL_M = 12.0
# Минимальная вероятность сохранить точку в разреженном пятне: не 0, чтобы
# не было проплешин.
_DENSITY_MIN_KEEP_PROBABILITY = 0.35


def _hash01(*parts: float, salt: str) -> float:
    """Детерминированное псевдослучайное число в [0, 1) -- чистая функция
    аргументов, не зависит от порядка вызовов (в отличие от random.Random)."""
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
    """Вероятность сохранить точку (x, z): 1 в густых пятнах шума, до
    _DENSITY_MIN_KEEP_PROBABILITY в разреженных. Удаление точек не уменьшает
    расстояния между оставшимися."""
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
    """Poisson-disk по Бридсону («Fast Poisson Disk Sampling in Arbitrary
    Dimensions», 2007): равномерный разброс без видимой сетки с минимальным
    расстоянием radius. rng -- один random.Random с фиксированным сидом."""
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
    """Хеш-сетка для проверки min_spacing между выбранными точками вместо
    перебора всех. Ячейка размером с min_spacing: все точки ближе min_spacing
    лежат в 9 соседних ячейках."""

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
