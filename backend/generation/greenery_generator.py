"""
Простая детерминированная генерация посадок для /api/generate-greenery:
деревья, группы кустарников и газон на свободной площади участка.

generate_trees, generate_bushes и generate_lawn принимают Scene и возвращают
только новые объекты. Эндпоинт вызывает их по очереди, добавляя результат
в сцену перед следующим шагом, -- иначе газон лёг бы поверх кустов.

Место под посадку -- разрешённые зоны (газон) минус запретные и
предупреждающие зоны с отступом по виду посадки (см. _keep_out_shapes).
Основной алгоритм озеленения -- GreenPlan (backend/greenplan/).
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

# Шаг кандидатов и минимальное расстояние между стволами по умолчанию, если
# запрос не передал свои.
DEFAULT_GRID_SPACING_M = 4.0
DEFAULT_MIN_TREE_SPACING_M = 4.0

# Предел числа деревьев: без размеченного газона генератор сажает по всему
# участку, и на гектарах это десятки тысяч деревьев сплошным ковром. Из
# годных мест берётся равномерно разбросанное подмножество (pick_spread).
MAX_GENERATED_TREES = 400

# Разумные границы на присланные параметры -- защита от вырожденных запросов
# (слишком маленький шаг -- тысячи точек и зависание на клике "Сгенерировать").
MIN_ALLOWED_GRID_SPACING_M = 0.5
MAX_ALLOWED_GRID_SPACING_M = 50.0


# Кустарник сажается группами: сетка кандидатов -- это центры групп, а в
# каждой группе BUSH_CLUSTER_SIZE кустов по кругу радиуса
# BUSH_CLUSTER_RADIUS_M. Одиночный куст посреди двора смотрится случайно.
DEFAULT_BUSH_GRID_SPACING_M = 2.5
DEFAULT_MIN_BUSH_SPACING_M = 2.5  # между ЦЕНТРАМИ групп, не между отдельными кустами внутри одной
BUSH_CLUSTER_SIZE = 3
BUSH_CLUSTER_RADIUS_M = 0.6
BUSH_EXISTING_CLEARANCE_M = 0.9  # меньше, чем у дерева -- куст компактнее и может стоять ближе к МАФ

# Предел числа групп кустов: иначе большой двор закатывается кустами без
# просвета газона.
MAX_GENERATED_BUSH_CLUSTERS = 60

# Газон -- плитки 4x4 м; проверяется вся плитка, а не только её центр.
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
    """Новые деревья на свободной площади сцены; scene не меняется. Посадить
    негде -- пустой список, а не ошибка.

    grid_spacing_m и min_tree_spacing_m по умолчанию -- DEFAULT_GRID_SPACING_M и
    DEFAULT_MIN_TREE_SPACING_M; значения вне разумных границ зажимаются.
    """
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
        return []

    # Вид не выбран -- смесь видов (DEFAULT_TREE_SPECIES_MIX); выбран -- он
    # для каждой точки.
    requested_species = species
    setback_species = [requested_species] if requested_species else [name for name, _ in DEFAULT_TREE_SPECIES_MIX]

    grid_spacing = grid_spacing_m if grid_spacing_m is not None else DEFAULT_GRID_SPACING_M
    grid_spacing = max(MIN_ALLOWED_GRID_SPACING_M, min(MAX_ALLOWED_GRID_SPACING_M, grid_spacing))

    min_spacing = min_tree_spacing_m if min_tree_spacing_m is not None else DEFAULT_MIN_TREE_SPACING_M
    min_spacing = max(0.0, min_spacing)

    keep_out = _keep_out_shapes(scene.restrictions, "tree", species=setback_species)
    keep_out += _existing_object_shapes(scene.objects)

    planting_zones = _planting_zone_shapes(scene.restrictions)
    # Есть размеченные зоны озеленения -- сажаем только в них, иначе -- по
    # всему участку минус запретные зоны.
    base_area = unary_union(planting_zones) if planting_zones else boundary_poly
    allowed_area = base_area.intersection(boundary_poly)
    if keep_out:
        allowed_area = allowed_area.difference(unary_union(keep_out))
    if allowed_area.is_empty:
        return []

    # prep() строит индекс один раз: на тысячах зон contains() без него
    # занимал минуты.
    allowed_area_ready = prep(allowed_area)

    # Кандидаты -- Poisson-disk разброс с фиксированным сидом: без рядов, но
    # воспроизводимо. Область -- рамка разрешённой площади, а не всей границы:
    # на вытянутых участках она намного меньше.
    min_x, min_z, max_x, max_z = allowed_area.bounds
    candidates = _poisson_disk_sample(min_x, min_z, max_x, max_z, grid_spacing, random.Random(_POISSON_SEED))

    selected_grid = _SpacingGrid(min_spacing)
    selected: list[tuple[float, float]] = []

    for cx, cz in candidates:
        point = Point(cx, cz)
        if not allowed_area_ready.contains(point):
            continue
        # Пятна гуще и реже (_keep_probability). Точки только удаляются,
        # поэтому минимальное расстояние не нарушается.
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
    """Новые группы кустов на свободной площади сцены. Вид у кустов не
    задаётся, отступы -- табличные для кустарника. Группа принимается или
    отклоняется целиком -- без частично осыпавшихся групп.
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

    # Индекс для contains(), как в generate_trees.
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

    # Равномерно разбросанное подмножество, а не первые по обходу сетки --
    # те легли бы в один угол двора.
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
    """Сплошной газон на свободной площади сцены. У газона нет корней и
    кроны, поэтому зоны вычитаются без отступа по виду посадки -- только сама
    охранная зона. Плитка 4x4 м проверяется целиком.
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
        # Плитка должна войти целиком: торчащая за край хуже пропущенного
        # места у границы.
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
