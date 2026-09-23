"""
Детерминированный demo-генератор посадок для /api/generate-greenery
(backend/main.py) -- реализует алгоритм из ТЗ п.16-17: деревья, кустарники
(меньшие расстояния и группировка) и газон (свободные зоны).

generate_trees/generate_bushes/generate_lawn независимы по контракту (каждая
принимает Scene, возвращает только НОВЫЕ объекты), но main.py вызывает их
ПОСЛЕДОВАТЕЛЬНО, каждый раз добавляя результат предыдущего шага в scene.objects
перед следующим вызовом -- иначе, например, газон лёг бы плиткой поверх уже
сгенерированных в этом же запросе кустов (см. _existing_object_shapes: она
учитывает вообще все объекты сцены, не различая "были до запроса" и
"добавлены этим же запросом раньше").

Кандидатная площадь для посадки -- это явно размеченные зоны озеленения
(severity == "allowed", газон/лужайка) МИНУС зоны, куда генератору сажать
нельзя (severity == "forbidden" ИЛИ "warning" -- парковка, дорожки, здания,
инженерные сети и т.п., каждая с отступом по виду посадки). Про то, почему
"warning" тоже исключается для АВТОгенератора, хотя для ручного
перетаскивания это остаётся лишь предупреждением -- см. докстринг
_keep_out_shapes() ниже.

Архитектура (см. модуль setback_norms.py) уже поддерживает разные виды
деревьев (species) с разными требованиями к отступам -- сейчас используется
один дефолтный вид без переопределений, добавление реальных видов не требует
правок в этом файле (см. докстринг setback_norms.py).

Позже вместо этого генератора можно подключить реальный ML/GIS-алгоритм --
main.py вызывает только generate_trees(scene, species), контракт функции
менять не обязательно.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Optional

from placement import pick_spread
from schemas import Point3, RestrictionZone, Scene, SceneObject
from setback_norms import DEFAULT_TREE_SPECIES, PlantKind, SpeciesArg, setback_for
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

# Дефолты шага сетки кандидатных точек и минимального расстояния между
# стволами -- используются, когда вызывающий код (main.py) не передал свои
# значения через параметры запроса. Подобраны как первое приближение для
# демо (ТЗ п.17: "минимальное расстояние между деревьями"); в бою обычно
# переопределяются per-request -- см. README.md ("Настраиваемые параметры").
DEFAULT_GRID_SPACING_M = 4.0
DEFAULT_MIN_TREE_SPACING_M = 4.0

# Та же защита, что у MAX_GENERATED_BUSH_CLUSTERS/MAX_GENERATED_LAWN_PATCHES
# ниже, изначально была только у них: на сцене без размеченных зон газона
# (severity == "allowed") генератор откатывается сажать по всей площади
# участка (см. planting_zones ниже) -- на реальных крупных участках (гектары,
# не тестовые дворы) сеткой по 4м это тысячи-десятки тысяч деревьев сплошным
# ковром на весь boundary, что и не похоже на продуманное благоустройство, и
# не годится для 3D-показа (десятки тысяч мешей). Как и у кустов/газона --
# после сбора всех геометрически годных мест берём не больше этого числа,
# равномерно раскиданных по площади (pick_spread), а не первые по порядку
# обхода сетки.
MAX_GENERATED_TREES = 400

# Разумные границы на присланные параметры -- защита от вырожденных запросов
# (слишком маленький шаг -- тысячи точек и зависание на клике "Сгенерировать").
MIN_ALLOWED_GRID_SPACING_M = 0.5
MAX_ALLOWED_GRID_SPACING_M = 50.0

# Небольшой запретный радиус вокруг уже существующих непостроечных объектов
# (лавочки, фонари, вручную расставленные деревья/кусты и т.п.), чтобы
# генератор не сажал дерево вплотную к ним. Здания сюда не входят -- для них
# уже есть отдельная зона restrictions с нормальным отступом по СНиП.
EXISTING_OBJECT_CLEARANCE_M = 1.5

# --- Кустарники (п.17 ТЗ: "меньшие расстояния и группировка") --------------
#
# В отличие от дерева, куст на сцене -- не одна точка, а группа из нескольких
# кустов вплотную друг к другу (так их и высаживают в реальном благоустройстве
# -- одиночный кустик посреди двора смотрится случайно, а группа читается как
# оформленная посадка). Поэтому у генератора кустов сетка кандидатов -- это
# сетка ЦЕНТРОВ ГРУПП, а не отдельных кустов: шаг между центрами меньше, чем
# у деревьев (кусту в принципе нужно меньше места), а внутри каждой принятой
# точки детерминированно раскладывается BUSH_CLUSTER_SIZE кустов по кругу
# радиуса BUSH_CLUSTER_RADIUS_M -- без учёта минимального расстояния друг с
# другом (это и есть группировка, а не россыпь).
DEFAULT_BUSH_GRID_SPACING_M = 2.5
DEFAULT_MIN_BUSH_SPACING_M = 2.5  # между ЦЕНТРАМИ групп, не между отдельными кустами внутри одной
BUSH_CLUSTER_SIZE = 3
BUSH_CLUSTER_RADIUS_M = 0.6
BUSH_EXISTING_CLEARANCE_M = 0.9  # меньше, чем у дерева -- куст компактнее и может стоять ближе к МАФ

# Сеткой по всей допустимой площади группы кустов на большом дворе легко
# набираются в тысячи (шаг всего 2.5 м) -- участок буквально "закатывается"
# кустами сплошным ковром без единого просвета газона, что не похоже на
# продуманное благоустройство и не годится для показа в 3D (тысячи мешей).
# Поэтому после сбора всех геометрически годных мест берём не больше
# MAX_GENERATED_BUSH_CLUSTERS, равномерно раскиданных по площади (pick_spread,
# k-средних из placement.py) -- та же идея, что и MAX_BULK_PLACEMENTS в
# llm_editor.py для операций правки текстом.
MAX_GENERATED_BUSH_CLUSTERS = 60

# --- Газон (п.17 ТЗ: "свободные зоны") --------------------------------------
#
# Сплошные плитки 4x4 м (как в каталоге -- plant_catalog.CATALOG, id
# "lawn_patch"), а не точки: проверяется весь прямоугольник плитки, а не
# только её центр -- иначе плитка могла бы наполовину висеть над дорожкой
# или высовываться за границу участка, хотя точка постановки формально верна.
DEFAULT_LAWN_PATCH_SIZE_M = 4.0
MIN_ALLOWED_LAWN_PATCH_SIZE_M = 1.0
MAX_ALLOWED_LAWN_PATCH_SIZE_M = 10.0
LAWN_EXISTING_CLEARANCE_M = 0.5  # маленький -- плитка газона вправе почти вплотную подходить к лавке/дереву

# Та же защита от вырожденно большого результата, что и у кустов выше --
# на большом открытом газоне плиток 4x4 м может набраться на сотни.
MAX_GENERATED_LAWN_PATCHES = 150


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


def _keep_out_shapes(
    restrictions: list[RestrictionZone], plant_kind: PlantKind, species: SpeciesArg = None
) -> list[Polygon]:
    """Полигоны зон, куда автогенератор не должен сажать растения данного
    вида (дерево или кустарник), каждый расширен наружу на положенный ЭТОМУ
    виду отступ -- для кустарника отступы в setback_norms.SETBACK_NORMS
    меньше, чем для дерева (п.17 ТЗ: "кустарники -- меньшие расстояния").

    Сюда идут и severity == "forbidden", И severity == "warning" (парковка,
    пешеходные дорожки, наземная ЛЭП и т.п.). Это осознанное расхождение с
    буквальным текстом исходного докстринга в main.py (там был только
    forbidden) -- у warning-зон есть отдельный смысл: они специально
    оставлены "предупреждение, а не запрет" для интерактивного
    перетаскивания на презентации (ТЗ п.8: пользователь может руками
    перетащить дерево на парковку/дорожку и увидеть жёлтое предупреждение).
    Но для АВТОгенератора это же самое "можно, но не нужно" означает, что
    сажать он туда не должен -- иначе на сгенерированной сцене деревья сразу
    стоят посреди парковки, что для демонстрации выглядит как баг, а не
    фича. Ручной drag&drop (frontend/src/geometry.ts::checkViolations) эту
    функцию не использует и по-прежнему трактует warning как
    "разрешено с предупреждением" -- поведение интерактивной проверки не
    меняется, меняется только то, что генератор сам туда не полезет.

    species -- все виды, которые могут встать на эту площадь; отступ от
    каждой зоны -- наибольший среди них (setback_norms.setback_for).
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid or poly.area == 0:
            continue
        setback = setback_for(zone.type, plant_kind, zone.minDistance, species=species)
        shapes.append(poly.buffer(setback) if setback > 0 else poly)
    return shapes


def _raw_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """То же самое, что и _keep_out_shapes, но БЕЗ отступа по виду посадки --
    для газона (setback_kind=None в каталоге, см. plant_catalog.py): у голого
    дёрна нет ни корней, ни кроны, поэтому норм в setback_norms.py для него
    нет, и достаточно не залезать на саму охранную зону (она уже отбуферена
    на minDistance сети самим парсером, см. parser/parse_dxf.py)."""
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            shapes.append(poly)
    return shapes


def _planting_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """Полигоны явно допустимых зон (severity == "allowed", например газон
    из слоя GRASS/LAWN -- см. parser/parse_dxf.py POLYGON_RULES). Если такие
    зоны в сцене размечены, генератор сажает только внутри них (плюс с
    учётом _keep_out_shapes) -- это и осмысленнее (дерево должно расти в
    зоне озеленения, а не просто "где угодно, где не запрещено"), и решает
    проблему избыточной плотности: вместо всей площади участка кандидатные
    точки ищутся только по факту размеченным под озеленение местам.
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity != "allowed" or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            shapes.append(poly)
    return shapes


def _existing_object_shapes(objects: list[SceneObject], clearance: float = EXISTING_OBJECT_CLEARANCE_M) -> list[Polygon]:
    """Небольшой круг-буфер вокруг каждого не-здания -- не даём генератору
    поставить новый объект поверх уже существующего. Здания намеренно
    исключены -- они уже покрыты своей zone type="building" в restrictions.

    clearance -- меньше для кустов/газона, чем для деревьев по умолчанию
    (EXISTING_OBJECT_CLEARANCE_M): дерево не должно стоять вплотную к лавке,
    а вот куст или плитка газона рядом с ней -- обычное дело.
    """
    return [
        Point(obj.position.x, obj.position.z).buffer(clearance)
        for obj in objects
        if obj.type != "building"
    ]


class _ZoneIndex:
    """Полигоны зон ограничений + STRtree для _placement_reason, построенные
    ОДИН РАЗ на вызов generate_trees/generate_bushes/generate_lawn, а не на
    каждую принятую точку. Раньше _placement_reason пересобирала Polygon для
    ВСЕХ зон заново на каждый вызов -- при тысячах принятых деревьев и
    десятках тысяч зон (плотные реальные данные, не тестовые локации) это
    было на порядок дороже, чем сами keep_out/allowed_area вычисления, и
    было главной причиной, почему generate-greenery не укладывался в разумное
    время. STRtree.nearest() -- O(log N) вместо линейного перебора всех зон
    на каждую точку."""

    def __init__(self, restrictions: list[RestrictionZone]):
        self.polys: list[Polygon] = []
        self.zones: list[RestrictionZone] = []
        for zone in restrictions:
            if len(zone.polygon) < 3:
                continue
            poly = Polygon([(p.x, p.z) for p in zone.polygon])
            if not poly.is_valid:
                continue
            self.polys.append(poly)
            self.zones.append(zone)
        self.tree = STRtree(self.polys) if self.polys else None


def _placement_reason(x: float, z: float, zone_index: _ZoneIndex) -> list[str]:
    """Человекочитаемое объяснение размещения для metadata.reason -- формат
    из ТЗ п.17 (пример: "внутри зоны озеленения", "4.2 м до водопровода").
    """
    reasons = ["внутри допустимой зоны озеленения"]
    if zone_index.tree is not None:
        pt = Point(x, z)
        idx = int(zone_index.tree.nearest(pt))
        poly = zone_index.polys[idx]
        zone = zone_index.zones[idx]
        distance = 0.0 if poly.contains(pt) else poly.exterior.distance(pt)
        reasons.append(f"{distance:.1f} м до ближайшего ограничения ({zone.name})")
    return reasons


def generate_trees(
    scene: Scene,
    species: Optional[str] = None,
    grid_spacing_m: Optional[float] = None,
    min_tree_spacing_m: Optional[float] = None,
) -> list[SceneObject]:
    """Сгенерировать новые деревья для свободной площади сцены.

    Ничего не меняет в scene -- возвращает только список НОВЫХ SceneObject
    для добавления вызывающим кодом (ТЗ п. "не трогать уже существующие
    объекты пользователя"). Если посадить негде (нет boundary, вся площадь
    занята ограничениями) -- возвращает пустой список, а не ошибку: это
    легитимный результат работы алгоритма, а не сбой.

    grid_spacing_m / min_tree_spacing_m -- настраиваемые параметры запроса
    (см. main.py, README.md); при None берутся дефолты DEFAULT_GRID_SPACING_M
    / DEFAULT_MIN_TREE_SPACING_M. Валидация диапазона (main.py уже проверяет
    через FastAPI Query) здесь дублируется мягко -- значения вне разумных
    границ тихо зажимаются, чтобы функция была безопасна и при прямом вызове
    из кода/тестов, в обход HTTP-слоя.
    """
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return []

    # None -- вызывающий код не выбрал конкретный вид явно, генератор вправе
    # смешивать виды на своё усмотрение (см. DEFAULT_TREE_SPECIES_MIX ниже).
    # Если species передан явно -- это выбор пользователя (UI), им и остаётся
    # для КАЖДОЙ точки, монокультурно, как и раньше.
    requested_species = species
    setback_species = [requested_species] if requested_species else [name for name, _ in DEFAULT_TREE_SPECIES_MIX]

    grid_spacing = grid_spacing_m if grid_spacing_m is not None else DEFAULT_GRID_SPACING_M
    grid_spacing = max(MIN_ALLOWED_GRID_SPACING_M, min(MAX_ALLOWED_GRID_SPACING_M, grid_spacing))

    min_spacing = min_tree_spacing_m if min_tree_spacing_m is not None else DEFAULT_MIN_TREE_SPACING_M
    min_spacing = max(0.0, min_spacing)

    keep_out = _keep_out_shapes(scene.restrictions, "tree", species=setback_species)
    keep_out += _existing_object_shapes(scene.objects)

    planting_zones = _planting_zone_shapes(scene.restrictions)
    # Если в сцене размечены явные зоны озеленения (газон и т.п.) -- сажаем
    # только внутри них. Если нет ни одной (нестандартная сцена без слоя
    # GRASS/LAWN) -- откатываемся на всю площадь участка минус keep_out,
    # чтобы генератор не оставался совсем без результата.
    base_area = unary_union(planting_zones) if planting_zones else boundary_poly
    allowed_area = base_area.intersection(boundary_poly)
    if keep_out:
        allowed_area = allowed_area.difference(unary_union(keep_out))
    if allowed_area.is_empty:
        return []

    # allowed_area на плотных реальных данных (тысячи зон ограничений) -- это
    # MultiPolygon с десятками тысяч вершин после unary_union/difference. Без
    # prepared-геометрии КАЖДЫЙ .contains(point) ниже перебирал бы все её
    # кольца заново -- на сетке кандидатов в тысячи точек по большому участку
    # это давало генерацию по 2+ минуты (не укладывалось в таймаут). prep()
    # строит пространственный индекс один раз для этой же геометрии -- тот же
    # результат contains(), на порядки быстрее при повторных запросах.
    allowed_area_ready = prep(allowed_area)

    # Раньше -- регулярная сетка кандидатов с шагом grid_spacing (детерминированно,
    # ТЗ п.16: "deterministic demo generator", без случайности). Смотрелось как
    # ряды даже после прореживания по min_spacing. Теперь -- Poisson-disk
    # (см. докстринг _poisson_disk_sample выше): органичный разброс кандидатов
    # той же плотности, но без выравнивания по сетке; ГСЧ детерминированный
    # (фиксированный сид), поэтому результат по-прежнему воспроизводим для
    # одной и той же геометрии и параметров.
    #
    # Область генерации -- bbox САМОЙ allowed_area, а не всей границы участка:
    # на вытянутых уличных проектах (реальный замер -- 02_peschany_pereulok,
    # 518x940 м bbox границы) плантуемая площадь обычно узкая полоса вдоль
    # дороги, а не весь bbox. Через boundary_poly.bounds Poisson-disk честно
    # заполнял ВЕСЬ bbox (19к точек, 2.2 с только на сам разброс) ради узкой
    # полосы, где реально приживалось меньше сотни -- через bbox allowed_area
    # тот же результат почти без лишней работы.
    min_x, min_z, max_x, max_z = allowed_area.bounds
    candidates = _poisson_disk_sample(min_x, min_z, max_x, max_z, grid_spacing, random.Random(_POISSON_SEED))

    selected_grid = _SpacingGrid(min_spacing)
    selected: list[tuple[float, float]] = []

    for cx, cz in candidates:
        point = Point(cx, cz)
        if not allowed_area_ready.contains(point):
            continue
        # Естественная неоднородность плотности (пятна гуще/реже) -- см.
        # докстринг _keep_probability. Только УДАЛЯЕТ точки из уже готового
        # Poisson-disk разброса, поэтому min_spacing между оставшимися не
        # нарушается.
        if _hash01(cx, cz, salt="density-thin") > _keep_probability(cx, cz):
            continue
        if not selected_grid.is_far_enough(cx, cz, min_spacing):
            continue
        selected_grid.add(cx, cz)
        selected.append((cx, cz))

    if len(selected) > MAX_GENERATED_TREES:
        selected = pick_spread(selected, MAX_GENERATED_TREES, min_spacing)

    new_objects: list[SceneObject] = []
    existing_tree_count = sum(1 for o in scene.objects if o.type == "tree")
    zone_index = _ZoneIndex(scene.restrictions)

    for cx, cz in selected:
        index = existing_tree_count + len(new_objects) + 1
        point_species = requested_species or _pick_species(cx, cz, DEFAULT_TREE_SPECIES_MIX)
        new_objects.append(
            SceneObject(
                id=f"tree_gen_{index:03d}",
                type="tree",
                model="/models/tree.glb",
                position=Point3(x=cx, y=0.0, z=cz),
                rotation=0.0,
                scale=1.0,
                metadata={
                    "species": point_species,
                    "category": "vegetation",
                    "generated": True,
                    "reason": _placement_reason(cx, cz, zone_index),
                },
            )
        )

    return new_objects


def generate_bushes(
    scene: Scene,
    grid_spacing_m: Optional[float] = None,
    min_bush_spacing_m: Optional[float] = None,
) -> list[SceneObject]:
    """Сгенерировать группы кустов для свободной площади сцены (п.17 ТЗ:
    "кустарники -- меньшие расстояния и группировка").

    В отличие от generate_trees, species-параметра нет: у сгенерированных
    кустов вид не задаётся (metadata без species), поэтому применяются
    табличные нормы для кустарника без правил по породе (setback_norms.py).

    Ничего не меняет в scene -- возвращает только НОВЫЕ SceneObject (тот же
    контракт, что и generate_trees). Место каждой принятой группы -- полный
    круг BUSH_CLUSTER_SIZE кустов радиуса BUSH_CLUSTER_RADIUS_M вокруг точки
    сетки: группа принимается ЦЕЛИКОМ (все её кусты внутри допустимой
    площади) или отклоняется целиком -- без частично осыпавшихся групп, где
    часть кустов легла, а часть за краем участка потерялась молча.
    """
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return []

    grid_spacing = grid_spacing_m if grid_spacing_m is not None else DEFAULT_BUSH_GRID_SPACING_M
    grid_spacing = max(MIN_ALLOWED_GRID_SPACING_M, min(MAX_ALLOWED_GRID_SPACING_M, grid_spacing))

    min_spacing = min_bush_spacing_m if min_bush_spacing_m is not None else DEFAULT_MIN_BUSH_SPACING_M
    min_spacing = max(0.0, min_spacing)

    keep_out = _keep_out_shapes(scene.restrictions, "bush")
    keep_out += _existing_object_shapes(scene.objects, clearance=BUSH_EXISTING_CLEARANCE_M)

    planting_zones = _planting_zone_shapes(scene.restrictions)
    base_area = unary_union(planting_zones) if planting_zones else boundary_poly
    allowed_area = base_area.intersection(boundary_poly)
    if keep_out:
        allowed_area = allowed_area.difference(unary_union(keep_out))
    if allowed_area.is_empty:
        return []

    # см. комментарий у prep() в generate_trees -- тот же приём, нужен здесь
    # по той же причине (плотные реальные данные -> тысячи проверок contains()
    # против сложной геометрии).
    allowed_area_ready = prep(allowed_area)

    min_x, min_z, max_x, max_z = boundary_poly.bounds

    candidates: list[tuple[float, float]] = []
    x = min_x + grid_spacing / 2
    while x < max_x:
        z = min_z + grid_spacing / 2
        while z < max_z:
            candidates.append((x, z))
            z += grid_spacing
        x += grid_spacing

    # Смещения кустов внутри одной группы -- равномерно по кругу, детерминированно
    # (без случайности, как и вся эта сцена, чтобы результат был воспроизводим).
    member_offsets = [
        (
            BUSH_CLUSTER_RADIUS_M * math.cos(2 * math.pi * i / BUSH_CLUSTER_SIZE),
            BUSH_CLUSTER_RADIUS_M * math.sin(2 * math.pi * i / BUSH_CLUSTER_SIZE),
        )
        for i in range(BUSH_CLUSTER_SIZE)
    ]

    selected_centers: list[tuple[float, float]] = []
    selected_grid = _SpacingGrid(min_spacing)
    for cx, cz in candidates:
        if not selected_grid.is_far_enough(cx, cz, min_spacing):
            continue
        members = [(cx + dx, cz + dz) for dx, dz in member_offsets]
        if not all(allowed_area_ready.contains(Point(mx, mz)) for mx, mz in members):
            continue
        selected_centers.append((cx, cz))
        selected_grid.add(cx, cz)

    # Геометрически годных мест обычно намного больше, чем стоит реально
    # занять кустами (см. докстринг MAX_GENERATED_BUSH_CLUSTERS) -- берём
    # равномерно раскиданное по площади подмножество, а не первые по порядку
    # обхода сетки (те легли бы одним углом двора).
    if len(selected_centers) > MAX_GENERATED_BUSH_CLUSTERS:
        selected_centers = pick_spread(selected_centers, MAX_GENERATED_BUSH_CLUSTERS, min_spacing)

    new_objects: list[SceneObject] = []
    existing_bush_count = sum(1 for o in scene.objects if o.type == "bush")
    zone_index = _ZoneIndex(scene.restrictions)
    for cx, cz in selected_centers:
        for dx, dz in member_offsets:
            mx, mz = cx + dx, cz + dz
            index = existing_bush_count + len(new_objects) + 1
            new_objects.append(
                SceneObject(
                    id=f"bush_gen_{index:03d}",
                    type="bush",
                    model="/models/bush.glb",
                    position=Point3(x=mx, y=0.0, z=mz),
                    rotation=0.0,
                    scale=1.0,
                    metadata={
                        "category": "vegetation",
                        "generated": True,
                        "reason": _placement_reason(mx, mz, zone_index),
                    },
                )
            )

    return new_objects


def generate_lawn(scene: Scene, patch_size_m: Optional[float] = None) -> list[SceneObject]:
    """Сгенерировать сплошной газон на свободной площади сцены (п.17 ТЗ:
    "газон -- свободные зоны").

    В отличие от generate_trees/generate_bushes, у газона нет ни setback_kind
    в каталоге, ни записи в setback_norms.py -- у голого дёрна нет ни корней,
    ни кроны, поэтому запретные зоны берутся БЕЗ дополнительного отступа по
    виду посадки (см. _raw_zone_shapes) -- только сама охранная зона, как её
    разметил парсер.

    Плитка 4x4 м (как и в каталоге) проверяется ВСЕМ своим прямоугольником
    (см. докстринг _raw_zone_shapes/BUSH_CLUSTER выше про то же для кустов) --
    иначе плитка могла бы наполовину висеть над дорожкой при формально верной
    точке постановки в центре.
    """
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return []

    size = patch_size_m if patch_size_m is not None else DEFAULT_LAWN_PATCH_SIZE_M
    size = max(MIN_ALLOWED_LAWN_PATCH_SIZE_M, min(MAX_ALLOWED_LAWN_PATCH_SIZE_M, size))
    half = size / 2

    keep_out = _raw_zone_shapes(scene.restrictions)
    keep_out += _existing_object_shapes(scene.objects, clearance=LAWN_EXISTING_CLEARANCE_M)

    planting_zones = _planting_zone_shapes(scene.restrictions)
    base_area = unary_union(planting_zones) if planting_zones else boundary_poly
    allowed_area = base_area.intersection(boundary_poly)
    if keep_out:
        allowed_area = allowed_area.difference(unary_union(keep_out))
    if allowed_area.is_empty:
        return []

    # см. комментарий у prep() в generate_trees.
    allowed_area_ready = prep(allowed_area)

    min_x, min_z, max_x, max_z = boundary_poly.bounds

    candidates: list[tuple[float, float]] = []
    x = min_x + half
    while x < max_x:
        z = min_z + half
        while z < max_z:
            candidates.append((x, z))
            z += size
        x += size

    fitting: list[tuple[float, float]] = []
    for cx, cz in candidates:
        tile = box(cx - half, cz - half, cx + half, cz + half)
        # Полное вхождение плитки, а не пересечение -- та же логика, что и у
        # Placer.region_contains в placement.py: частично торчащая за край
        # плитка хуже, чем пропущенное место у самой границы.
        if allowed_area_ready.contains(tile):
            fitting.append((cx, cz))

    # Та же защита от вырожденно большого результата, что и у кустов выше.
    if len(fitting) > MAX_GENERATED_LAWN_PATCHES:
        fitting = pick_spread(fitting, MAX_GENERATED_LAWN_PATCHES, size)

    new_objects: list[SceneObject] = []
    existing_lawn_count = sum(1 for o in scene.objects if o.type == "lawn_patch")
    zone_index = _ZoneIndex(scene.restrictions)

    for cx, cz in fitting:
        index = existing_lawn_count + len(new_objects) + 1
        new_objects.append(
            SceneObject(
                id=f"lawn_gen_{index:03d}",
                type="lawn_patch",
                model="/models/lawn_patch.glb",
                position=Point3(x=cx, y=0.0, z=cz),
                rotation=0.0,
                scale=1.0,
                metadata={
                    "category": "groundcover",
                    "generated": True,
                    "reason": _placement_reason(cx, cz, zone_index),
                },
            )
        )

    return new_objects
