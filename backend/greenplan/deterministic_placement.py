"""
Детерминированная расстановка новой посадки, без LLM: точки считает
геометрия. Каждая зона уже получила приём (pattern_assignment); здесь пара
(зона, приём) превращается в список SceneObject. Норма отступа проверяется у
каждой точки во время расстановки (Placer.is_free) с учётом породы -- липе
10 м от здания и т. п., см. setback_norms.py.

Placer строится один раз из той же сцены, что ушла в partition_zones, --
характеристика участка, разбиение и расстановка сверяются с одной геометрией.

Три семейства приёмов (geometry_family из pattern_library.py):

* linear    -- ряды точек вдоль оси зоны, по желанию -- второй ряд
               (PatternSpec.double_row_offset_m);
* area_fill -- кандидаты по всей площади зоны и равномерный отбор (pick_spread);
* clustered -- несколько центров по зоне, у каждого -- компактная группа
               одного вида (pick_near).

Виды растений подбирает species_selection.py по ассортименту Москвы для типа
территории, без инвазивных видов 369-ПП. Функции place_zone/_place_* знают
только геометрию и сажают то, что им передали.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from shapely.geometry import Polygon

from core.placement import Placer
from core.placement_geometry import (
    centerline_of,
    concentric_rings_of,
    lines_of,
    pick_near,
    pick_spread,
    rect_sides,
    rotation_of,
    rows_of,
    sample_line,
    wavy_line,
)
from core.plant_catalog import CatalogItem
from core.schemas import Point3, Scene, SceneObject
from greenplan.explanations import record_rejection
from greenplan.pattern_assignment import ZoneAssignment, assign_patterns
from greenplan.pattern_library import PATTERN_LIBRARY, PatternSpec
from greenplan.site_characterization import characterize_site, usable_planting_area
from greenplan.species_selection import SiteContext, SpeciesPalette, preference_note, select_site_species, site_key
from greenplan.zone_partitioning import GeometricZone, partition_zones

log = logging.getLogger("greencity.greenplan")

# Шаг кустов вдоль линии и во сколько раз реже идут деревья -- по реальным
# проектам (02_peschany_pereulok: кусты 1,4 м, деревья 12 м).
LINEAR_BUSH_STEP_M = 1.4
LINEAR_TREE_STEP_FACTOR = 8.5

# Расстояние между параллельными рядами на open_area -- как шаг площадной
# заливки, чтобы плотность линейных и площадных приёмов была сопоставима.
OPEN_AREA_ROW_SPACING_M = 6.0
# Предел рядов на одну open_area-зону: без него огромная зона получала
# десятки рядов по сотням кустов.
MAX_ROWS_PER_ZONE = 10

# Площадная заливка: шаг сетки кандидатов и во сколько раз реже деревья --
# по 20_makeeva_s (кусты 6 м / деревья 12 м, 2x).
AREA_FILL_BUSH_SPACING_M = 6.0
AREA_FILL_TREE_SPACING_M_FACTOR = 2.0

# Рощи: площадь на одну группу и разнос между центрами/членами группы --
# по 12_natashinsky_proezd (25 роще на явно небольшой открытой площади).
CLUSTER_AREA_PER_GROUP_SQM = 200.0
CLUSTER_CENTER_SPACING_M = 8.0
CLUSTER_MEMBER_SPACING_M = 2.0

# Предел объектов и групп на одну зону: на огромной зоне (район 2 км²)
# заливка требовала бы десятки тысяч точек, а pick_spread (k-средних) на
# таком k работает минутами. Сверх предела зона получает столько объектов,
# сколько помещается при разумной плотности.
MAX_OBJECTS_PER_ZONE = 400
MAX_GROUPS_PER_ZONE = 60

# Предел новой посадки на весь участок: когда зон сотни, предел на зону не
# спасает. Для сравнения: в реальном проекте на 48 га -- около 2,5 тыс. посадок.
MAX_NEW_OBJECTS_PER_SITE = 10_000


def _polygon_of(zone: GeometricZone) -> Polygon:
    return Polygon([(p.x, p.z) for p in zone.polygon])


# Разброс размера деревьев (доля): в свободных посадках заметный, в
# регулярных -- небольшой, чтобы не терялась строгость рисунка.
TREE_SCALE_JITTER_NATURAL = 0.15
TREE_SCALE_JITTER_FORMAL = 0.06


def _tree_scale_jitter(spec: PatternSpec) -> float:
    natural = spec.geometry_family == "clustered" or (spec.geometry_family == "area_fill" and not spec.dense_lattice)
    return TREE_SCALE_JITTER_NATURAL if natural else TREE_SCALE_JITTER_FORMAL


def _scene_object(
    key: str, item: CatalogItem, x: float, z: float, rotation: float, zone: GeometricZone, assignment: ZoneAssignment
) -> SceneObject:
    scale = 1.0
    if item.object_type == "tree":
        # Одинаковые деревья одного размера и поворота выглядят клонами:
        # размер и поворот -- с разбросом, как у реального посадочного
        # материала. Детерминированно: сид -- ключ и место объекта.
        rng = random.Random(f"{key}|{x:.1f}|{z:.1f}")
        rotation = round(rng.uniform(0.0, 360.0), 1)
        jitter = _tree_scale_jitter(PATTERN_LIBRARY[assignment.pattern_id])
        scale = round(rng.uniform(1 - jitter, 1 + jitter), 3)
    return SceneObject(
        id=key,
        type=item.object_type,
        model=item.model,
        position=Point3(x=x, y=0.0, z=z),
        rotation=rotation,
        scale=scale,
        metadata={
            "species": item.label,
            "category": "vegetation",
            "generated": True,
            "source": "greenplan",  # по нему повторный запуск убирает прошлый результат
            "catalogId": item.id,
            "pattern_id": assignment.pattern_id,
            "zone_id": zone.id,
            "source_project": assignment.source_project,
        },
    )


def _free_points(placer: Placer, points: list[tuple[float, float]], kind: str, obj_type: str) -> list[tuple[float, float]]:
    """Кандидаты, не занятые уже стоящими объектами: иначе на участке с
    сотнями существующих деревьев центр рощи почти всегда попадал вплотную
    к старому дереву и группа не вставала. Отступ по породе проверяет
    is_free при посадке каждого вида."""
    return [(x, z) for x, z in points if placer.is_free(x, z, kind, obj_type=obj_type)]


def _place_row(
    placer: Placer,
    zone: GeometricZone,
    assignment: ZoneAssignment,
    items: list[CatalogItem],
    kind: str,
    step: float,
    row_offset: float,
    line,
    counter: list[int],
) -> list[SceneObject]:
    if not items:
        return []
    objects = []
    for x, z, tx, tz in sample_line(line, step):
        px, pz = x - tz * row_offset, z + tx * row_offset
        item = items[counter[0] % len(items)]
        if not placer.is_free(px, pz, kind, obj_type=item.object_type, species=item.label):
            record_rejection(placer, px, pz, item.object_type, item.label, zone.kind, assignment.pattern_id)
            continue
        counter[0] += 1
        key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
        placer.occupy(key, px, pz, item.object_type)
        objects.append(_scene_object(key, item, px, pz, rotation_of(tx, tz), zone, assignment))
    return objects


def _place_linear(
    placer: Placer,
    zone: GeometricZone,
    spec: PatternSpec,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    poly = _polygon_of(zone)

    if spec.line_shape == "ring":
        # Кольцо у здания -- собственный контур зоны: линия через центроид
        # срезала бы кольцо хордой.
        row_lines = [poly.exterior]
    elif spec.line_shape == "concentric":
        # Несколько вложенных колец вокруг центра зоны.
        row_lines = concentric_rings_of(poly, spec.ring_spacing_m)
        if len(row_lines) > MAX_ROWS_PER_ZONE:
            row_lines = row_lines[:MAX_ROWS_PER_ZONE]  # ближайшие к центру кольца, не случайные
    elif zone.kind == "open_area":
        # open_area -- широкое пятно, а не узкая полоса: одна линия через
        # середину оставила бы пустой остальную площадь, поэтому рядов
        # столько, сколько помещается по ширине.
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        row_lines = rows_of(poly, OPEN_AREA_ROW_SPACING_M, angle_offset_deg=angle)
        if len(row_lines) > MAX_ROWS_PER_ZONE:
            # Прореживаем равномерно, а не берём первые ряды, -- иначе
            # засажена была бы только одна сторона зоны.
            step = len(row_lines) / MAX_ROWS_PER_ZONE
            row_lines = [row_lines[round(i * step)] for i in range(MAX_ROWS_PER_ZONE)]
    else:
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        centerline = centerline_of(poly, angle_offset_deg=angle)
        row_lines = [centerline] if centerline is not None else []

    if spec.line_shape == "wavy" and spec.wave_amplitude_m > 0:
        # Амплитуда волны из корпуса бывает шире самой зоны: урезаем её под
        # ширину зоны, иначе после обрезки по контуру волна рассыпается.
        short_side = min(s[2] for s in rect_sides(poly))
        amplitude = min(spec.wave_amplitude_m, short_side * 0.35)
        wavy = [wavy_line(line, amplitude, spec.wave_length_m) for line in row_lines]
        row_lines = [w.intersection(poly) for w in wavy if w is not None]

    if not row_lines:
        return []
    offsets = (0.0,) if spec.double_row_offset_m <= 0 else (0.0, spec.double_row_offset_m)
    tree_step = spec.tree_step_m if spec.tree_step_m is not None else LINEAR_BUSH_STEP_M * LINEAR_TREE_STEP_FACTOR
    # Предел объектов на зону соблюдаем увеличением шага, а не обрезкой
    # рядов: так засажены все ряды по всей зоне. Узкие полосы в предел
    # укладываются и остаются с шагом 1,4 м.
    total_length = sum(line.length for line in row_lines)
    tree_step = max(tree_step, total_length / MAX_OBJECTS_PER_ZONE)
    bush_step = max(LINEAR_BUSH_STEP_M, total_length * len(offsets) / MAX_OBJECTS_PER_ZONE)
    objects: list[SceneObject] = []
    bush_counter, tree_counter = [0], [0]
    for row_centerline in row_lines:
        for line in lines_of(row_centerline):
            # Деревья сажаем раньше кустов: кусты с шагом 1,4 м занимают всю
            # линию, и после них ни одна точка для дерева не прошла бы
            # проверку зазора.
            objects += _place_row(placer, zone, assignment, trees, "tree", tree_step, 0.0, line, tree_counter)
            # trees_only (formal_bosque_grid) -- боскет это открытая сетка
            # стволов, без кустарникового яруса под кроной по смыслу паттерна.
            if not spec.trees_only:
                for row_offset in offsets:
                    objects += _place_row(placer, zone, assignment, bushes, "bush", bush_step, row_offset, line, bush_counter)
    return objects


def _place_area_fill(
    placer: Placer,
    zone: GeometricZone,
    spec: PatternSpec,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    poly = _polygon_of(zone)
    if poly.is_empty or poly.area <= 0:
        return []
    objects: list[SceneObject] = []
    for items, kind, spacing in (
        (bushes, "bush", AREA_FILL_BUSH_SPACING_M),
        (trees, "tree", AREA_FILL_BUSH_SPACING_M * AREA_FILL_TREE_SPACING_M_FACTOR),
    ):
        if not items or (kind == "bush" and spec.trees_only):
            continue
        target = min(max(0, round(poly.area / spacing**2)), MAX_OBJECTS_PER_ZONE)
        if target == 0:
            continue
        if spec.dense_lattice:
            # Треугольная сетка берёт точки прямо с решётки: ей нужен видимый
            # регулярный узор, а не равномерный разброс pick_spread.
            chosen = placer.points_in_area(kind, spacing, within=poly, max_candidates=MAX_OBJECTS_PER_ZONE)
        else:
            candidates = placer.points_in_area(kind, spacing / 2, within=poly, max_candidates=target * 20)
            candidates = _free_points(placer, candidates, kind, items[0].object_type)
            chosen = pick_spread(candidates, target, spacing * 0.8)
        counter = [0]
        for x, z in chosen:
            item = items[counter[0] % len(items)]
            if not placer.is_free(x, z, kind, obj_type=item.object_type, species=item.label):
                record_rejection(placer, x, z, item.object_type, item.label, zone.kind, assignment.pattern_id)
                continue
            counter[0] += 1
            key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
            placer.occupy(key, x, z, item.object_type)
            objects.append(_scene_object(key, item, x, z, 0.0, zone, assignment))
    return objects


def _place_clustered(
    placer: Placer,
    zone: GeometricZone,
    spec: PatternSpec,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    items = trees or bushes
    if not items:
        return []
    kind = items[0].setback_kind
    poly = _polygon_of(zone)
    if poly.is_empty or poly.area <= 0:
        return []

    group_count = min(max(1, round(poly.area / CLUSTER_AREA_PER_GROUP_SQM)), MAX_GROUPS_PER_ZONE)
    pool = placer.points_in_area(kind, CLUSTER_MEMBER_SPACING_M / 2, within=poly, max_candidates=group_count * 60)
    pool = _free_points(placer, pool, kind, items[0].object_type)
    if not pool:
        return []
    centers = pick_spread(pool, group_count, CLUSTER_CENTER_SPACING_M)

    objects: list[SceneObject] = []
    counter = [0]
    for group_index, center in enumerate(centers):
        item = items[group_index % len(items)]  # один вид на всю группу -- как в 19_2ya_pryadilnaya
        target_size = spec.group_size[1]
        members = pick_near(pool, target_size, CLUSTER_MEMBER_SPACING_M, center)
        for x, z in members:
            if not placer.is_free(x, z, kind, obj_type=item.object_type, species=item.label):
                record_rejection(placer, x, z, item.object_type, item.label, zone.kind, assignment.pattern_id)
                continue
            counter[0] += 1
            key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
            placer.occupy(key, x, z, item.object_type)
            objects.append(_scene_object(key, item, x, z, (counter[0] * 137.5) % 360, zone, assignment))
    return objects


def place_zone(
    placer: Placer,
    zone: GeometricZone,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    spec = PATTERN_LIBRARY[assignment.pattern_id]
    if spec.geometry_family == "linear":
        return _place_linear(placer, zone, spec, assignment, trees, bushes)
    if spec.geometry_family == "clustered":
        return _place_clustered(placer, zone, spec, assignment, trees, bushes)
    return _place_area_fill(placer, zone, spec, assignment, trees, bushes)


@dataclass
class SitePlan:
    objects: list[SceneObject]
    assignments: list[ZoneAssignment]
    # Для пользователя: почему предпочтительные виды не попали в посадку.
    notes: list[str] = field(default_factory=list)


def generate_for_scene(
    scene: Scene,
    trees: list[CatalogItem] | None = None,
    bushes: list[CatalogItem] | None = None,
    k: int = 3,
) -> tuple[list[SceneObject], list[ZoneAssignment]]:
    """Зонирование, назначение приёмов и расстановка на одной сцене.
    Возвращает только новые объекты (добавлять ли их в scene.objects, решает
    вызывающий код) и решения по зонам. С параметрами пользователя -- plan_site."""
    plan = plan_site(scene, trees, bushes, k)
    return plan.objects, plan.assignments


def plan_site(
    scene: Scene,
    trees: list[CatalogItem] | None = None,
    bushes: list[CatalogItem] | None = None,
    k: int = 3,
    style: str = "auto",
    preferred: list[CatalogItem] | None = None,
) -> SitePlan:
    """То же, что generate_for_scene, с параметрами пользователя: style --
    стиль участка ("auto" -- по похожим проектам), preferred -- виды,
    которые ставятся первыми в любой роли своей категории (если проходят
    нормы)."""
    preferred = preferred or []
    trees = trees or []
    bushes = bushes or []
    # Площадь под озеленение -- самая дорогая операция прохода: считаем
    # один раз и передаём в разбиение и поиск аналогов.
    usable = usable_planting_area(scene)
    zones = partition_zones(scene, usable=usable)
    characteristics = characterize_site(scene, usable=usable)
    assignments = assign_patterns(scene, zones, k=k, characteristics=characteristics, style=style)
    placer = Placer(scene)

    # Один набор видов на пару (вид зоны, приём) на всём участке: изгородь
    # вдоль всех дорожек -- из одного вида.
    site = SiteContext(
        territory_type=characteristics.territory_type if characteristics else "неопределено",
        has_playground=any(zone.type == "playground_zone" for zone in scene.restrictions),
        site_key=site_key(scene),
        preferred=frozenset(item.label for item in preferred),
    )
    # Единая палитра видов на участок: крупнейшие решения задают её первыми.
    area_by_key: dict[tuple[str, str], float] = {}
    for zone, assignment in zip(zones, assignments):
        key = (zone.kind, assignment.pattern_id)
        area_by_key[key] = area_by_key.get(key, 0.0) + zone.area_sqm
    palettes: dict[tuple[str, str], SpeciesPalette] = select_site_species(
        sorted(area_by_key, key=lambda key: -area_by_key[key]), trees, bushes, site
    )

    objects: list[SceneObject] = []
    placed_assignments: list[ZoneAssignment] = []
    for zone, assignment in zip(zones, assignments):
        palette = palettes[(zone.kind, assignment.pattern_id)]
        objects += place_zone(placer, zone, assignment, palette.trees, palette.bushes)
        placed_assignments.append(
            assignment.model_copy(
                update={
                    "tree_species": [item.label for item in palette.trees],
                    "bush_species": [item.label for item in palette.bushes],
                    "species_basis": palette.basis,
                }
            )
        )
    objects = _thin_to_site_cap(objects)
    planted = {obj.metadata.get("species") for obj in objects}
    kinds = [zone.kind for zone in zones]
    notes = [preference_note(item, kinds, site) for item in preferred if item.label not in planted]
    return SitePlan(objects=objects, assignments=placed_assignments, notes=notes)


def _thin_to_site_cap(objects: list[SceneObject]) -> list[SceneObject]:
    """Равномерное прореживание сверх MAX_NEW_OBJECTS_PER_SITE: каждый n-й
    объект в порядке расстановки (зона за зоной, вдоль рядов), поэтому доля
    каждой зоны и соотношение деревьев и кустов сохраняются, а ряды
    становятся реже, но не обрываются. Удаление посадки не может создать
    нарушение отступов -- повторная проверка не нужна."""
    if len(objects) <= MAX_NEW_OBJECTS_PER_SITE:
        return objects
    step = len(objects) / MAX_NEW_OBJECTS_PER_SITE
    log.info("GreenPlan: %d новых объектов > %d на участок -- прорежено", len(objects), MAX_NEW_OBJECTS_PER_SITE)
    return [objects[int(i * step)] for i in range(MAX_NEW_OBJECTS_PER_SITE)]
