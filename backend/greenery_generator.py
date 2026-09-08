"""
Детерминированный demo-генератор посадок для /api/generate-greenery
(backend/main.py) -- реализует алгоритм из ТЗ п.16-17 для деревьев.

Пока реализованы ТОЛЬКО деревья. Кустарники и газон (п.17 ТЗ: кустарники --
меньшие расстояния и группировка, газон -- свободные зоны) сознательно
оставлены на следующий проход -- сюда должны лечь generate_bushes()/
generate_lawn() рядом с generate_trees(), не трогая её.

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

from typing import Optional

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from schemas import Point3, RestrictionZone, Scene, SceneObject
from setback_norms import DEFAULT_TREE_SPECIES, setback_for

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


def _keep_out_shapes(
    restrictions: list[RestrictionZone], species: Optional[str]
) -> list[Polygon]:
    """Полигоны зон, куда автогенератор не должен сажать деревья, каждый
    расширен наружу на положенный дереву отступ.

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
        setback = setback_for(zone.type, "tree", zone.minDistance, species=species)
        shapes.append(poly.buffer(setback) if setback > 0 else poly)
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


def _existing_object_shapes(objects: list[SceneObject]) -> list[Polygon]:
    """Небольшой круг-буфер вокруг каждого не-здания -- не даём генератору
    поставить новое дерево поверх существующего объекта. Здания намеренно
    исключены -- они уже покрыты своей zone type="building" в restrictions.
    """
    return [
        Point(obj.position.x, obj.position.z).buffer(EXISTING_OBJECT_CLEARANCE_M)
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

    keep_out = _keep_out_shapes(scene.restrictions, species)
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
