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
from typing import Optional

from placement import pick_spread
from schemas import Point3, RestrictionZone, Scene, SceneObject
from setback_norms import DEFAULT_TREE_SPECIES, PlantKind, setback_for
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

# Дефолты шага сетки кандидатных точек и минимального расстояния между
# стволами -- используются, когда вызывающий код (main.py) не передал свои
# значения через параметры запроса. Подобраны как первое приближение для
# демо (ТЗ п.17: "минимальное расстояние между деревьями"); в бою обычно
# переопределяются per-request -- см. README.md ("Настраиваемые параметры").
DEFAULT_GRID_SPACING_M = 4.0
DEFAULT_MIN_TREE_SPACING_M = 4.0

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


def _keep_out_shapes(
    restrictions: list[RestrictionZone], plant_kind: PlantKind, species: Optional[str] = None
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


def _placement_reason(x: float, z: float, restrictions: list[RestrictionZone]) -> list[str]:
    """Человекочитаемое объяснение размещения для metadata.reason -- формат
    из ТЗ п.17 (пример: "внутри зоны озеленения", "4.2 м до водопровода").
    """
    reasons = ["внутри допустимой зоны озеленения"]
    nearest: tuple[float, RestrictionZone] | None = None
    for zone in restrictions:
        if len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid:
            continue
        pt = Point(x, z)
        distance = 0.0 if poly.contains(pt) else poly.exterior.distance(pt)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, zone)
    if nearest is not None:
        distance, zone = nearest
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

    species = species or DEFAULT_TREE_SPECIES

    grid_spacing = grid_spacing_m if grid_spacing_m is not None else DEFAULT_GRID_SPACING_M
    grid_spacing = max(MIN_ALLOWED_GRID_SPACING_M, min(MAX_ALLOWED_GRID_SPACING_M, grid_spacing))

    min_spacing = min_tree_spacing_m if min_tree_spacing_m is not None else DEFAULT_MIN_TREE_SPACING_M
    min_spacing = max(0.0, min_spacing)

    keep_out = _keep_out_shapes(scene.restrictions, "tree", species=species)
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

    min_x, min_z, max_x, max_z = boundary_poly.bounds

    # Регулярная сетка кандидатов -- детерминированно (ТЗ п.16: "deterministic
    # demo generator"), без случайности, чтобы результат был воспроизводим.
    candidates: list[tuple[float, float]] = []
    x = min_x + grid_spacing / 2
    while x < max_x:
        z = min_z + grid_spacing / 2
        while z < max_z:
            candidates.append((x, z))
            z += grid_spacing
        x += grid_spacing

    selected: list[tuple[float, float]] = []
    new_objects: list[SceneObject] = []
    existing_tree_count = sum(1 for o in scene.objects if o.type == "tree")

    for cx, cz in candidates:
        point = Point(cx, cz)
        if not allowed_area.contains(point):
            continue
        too_close = any(
            (cx - sx) ** 2 + (cz - sz) ** 2 < min_spacing**2 for sx, sz in selected
        )
        if too_close:
            continue

        selected.append((cx, cz))
        index = existing_tree_count + len(new_objects) + 1
        new_objects.append(
            SceneObject(
                id=f"tree_gen_{index:03d}",
                type="tree",
                model="/models/tree.glb",
                position=Point3(x=cx, y=0.0, z=cz),
                rotation=0.0,
                scale=1.0,
                metadata={
                    "species": species,
                    "category": "vegetation",
                    "generated": True,
                    "reason": _placement_reason(cx, cz, scene.restrictions),
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

    В отличие от generate_trees, species-параметра нет: setback_for()
    применяет SPECIES_SETBACK_OVERRIDES только для plant_kind == "tree" (см.
    setback_norms.py) -- для кустарника вид ни на что не влияет, добавлять
    параметр, который ничего не меняет, было бы лишним.

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
    for cx, cz in candidates:
        too_close = any(
            (cx - sx) ** 2 + (cz - sz) ** 2 < min_spacing**2 for sx, sz in selected_centers
        )
        if too_close:
            continue
        members = [(cx + dx, cz + dz) for dx, dz in member_offsets]
        if not all(allowed_area.contains(Point(mx, mz)) for mx, mz in members):
            continue
        selected_centers.append((cx, cz))

    # Геометрически годных мест обычно намного больше, чем стоит реально
    # занять кустами (см. докстринг MAX_GENERATED_BUSH_CLUSTERS) -- берём
    # равномерно раскиданное по площади подмножество, а не первые по порядку
    # обхода сетки (те легли бы одним углом двора).
    if len(selected_centers) > MAX_GENERATED_BUSH_CLUSTERS:
        selected_centers = pick_spread(selected_centers, MAX_GENERATED_BUSH_CLUSTERS, min_spacing)

    new_objects: list[SceneObject] = []
    existing_bush_count = sum(1 for o in scene.objects if o.type == "bush")
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
                        "reason": _placement_reason(mx, mz, scene.restrictions),
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
        if allowed_area.contains(tile):
            fitting.append((cx, cz))

    # Та же защита от вырожденно большого результата, что и у кустов выше.
    if len(fitting) > MAX_GENERATED_LAWN_PATCHES:
        fitting = pick_spread(fitting, MAX_GENERATED_LAWN_PATCHES, size)

    new_objects: list[SceneObject] = []
    existing_lawn_count = sum(1 for o in scene.objects if o.type == "lawn_patch")

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
                    "reason": _placement_reason(cx, cz, scene.restrictions),
                },
            )
        )

    return new_objects
