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

import math
import random
from typing import Optional

from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.prepared import prep

from core.placement_geometry import pick_spread
from core.schemas import Point3, Scene, SceneObject
from core.shapes import polygon_from_points
from generation.natural_sampling import (
    _POISSON_SEED,
    DEFAULT_TREE_SPECIES_MIX,
    _hash01,
    _keep_probability,
    _pick_species,
    _poisson_disk_sample,
    _SpacingGrid,
)
from generation.planting_area import (
    _existing_object_shapes,
    _keep_out_shapes,
    _placement_reason,
    _planting_zone_shapes,
    _raw_zone_shapes,
    _ZoneIndex,
)

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
# text_editor/service.py для операций правки текстом.
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

# Естественный разброс, шум плотности и смесь видов -- generation/natural_sampling.py;
# допустимая площадь и отступы -- generation/planting_area.py.


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

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
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

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
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

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
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
