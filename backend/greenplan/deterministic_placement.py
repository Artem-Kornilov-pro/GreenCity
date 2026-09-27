"""
Детерминированная расстановка новой посадки -- GreenPlan, Этап 5 (issue #23).
Полностью без LLM: точки считает геометрия, а не языковая модель (тот же
принцип, что и в placement.py -- см. его докстринг). Каждая зона
(zone_partitioning.GeometricZone) уже получила паттерн от pattern_assignment
(Этап 4); эта функция превращает пару (зона, паттерн) в список SceneObject,
проверяя нормативный отступ у КАЖДОЙ точки через placer.is_free ВО ВРЕМЯ
расстановки, с учётом породы (species=item.label -- правила по роду в
setback_norms.py: липе 10 м от здания и т.п.), а не постфактум-фильтром, как
одноразовые скрипты прошлой сессии (см. память проекта про `post_filter.py`).

`Placer` строится ОДИН РАЗ из того же `Scene`, что ушёл в `partition_zones` --
вся геометрия (характеризация участка, разбиение на зоны, расстановка) сверяется
с одним и тем же источником правды, поэтому класс бага "моя реконструкция
геометрии разошлась с реальным парсером" (прошлая сессия наступала на него в
каждом одноразовом генераторе) здесь структурно исключён.

Три geometry_family из pattern_library.py -- три алгоритма, каждый по одному
единому семейству паттернов (issue #23, Этап 5: "линейные паттерны -- один
параметризуемый алгоритм", "площадные заливки -- Пуассоновское распределение
или сетка"):

* linear    -- ряд(ы) точек вдоль centerline_of(зона) (placement.py), с
               опциональной второй линией (PatternSpec.double_row_offset_m).
* area_fill -- кандидаты по всей площади зоны (Placer.points_in_area) + ровный
               отбор (pick_spread).
* clustered -- несколько центров по зоне (points_in_area + pick_spread), у
               каждого -- компактная группа соседей (pick_near), один вид
               растения на группу.

Виды растений: вызывающий код передаёт весь доступный каталог (trees/bushes),
а generate_for_scene подбирает из него виды под каждую пару (вид зоны,
паттерн) через species_selection.py -- по ассортименту Москвы для типа
территории, без инвазивных видов 369-ПП. Функции place_zone/_place_* ниже
знают только геометрию и сажают то, что им передали.
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

# Шаг ряда кустов вдоль линейного паттерна и во сколько раз реже вдоль той же
# линии идут деревья -- по реальному соотношению из 02_peschany_pereulok
# (кусты 1.4 м / деревья 12 м, ~8.5x) и 19_2ya_pryadilnaya (изгородь у дороги).
LINEAR_BUSH_STEP_M = 1.4
LINEAR_TREE_STEP_FACTOR = 8.5

# Расстояние между параллельными рядами для linear-паттерна на open_area
# (rows_of вместо одной центральной линии, см. её докстринг) -- то же
# расстояние, что и у шага заливки массива (AREA_FILL_BUSH_SPACING_M ниже),
# для сопоставимой плотности между линейными и площадными паттернами на
# одном и том же виде зоны.
OPEN_AREA_ROW_SPACING_M = 6.0
# Верхняя граница числа рядов на одну open_area-зону -- без неё гигантская
# зона (тот же реальный случай 646 279 м², что и у MAX_OBJECTS_PER_ZONE ниже)
# даёт 36 рядов по многие сотни кустов КАЖДЫЙ (один длинный ряд сам по себе
# уже плотный при шаге LINEAR_BUSH_STEP_M=1.4 м) -- полный пайплайн на этом
# файле подскочил с ~11 до ~20 секунд и почти 9000 новых объектов на одну
# сцену при добавлении rows_of (было: одна линия, тот же баг класса "гигант-
# ская зона", что и у MAX_OBJECTS_PER_ZONE/MAX_GROUPS_PER_ZONE, только для
# другого geometry_family). 10 рядов даёт сопоставимый с area_fill/clustered
# порядок итоговой плотности на такой зоне вместо кратного превышения.
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

# Верхняя граница числа объектов/групп на ОДНУ зону -- без неё площадная
# заливка или рощи на очень крупной зоне (найдено на реальном случае:
# locations/location_old/05_klykova_avenue, район 2 км², одна open_area зона
# 646 279 м²) требуют target ~18 000, а pick_spread -- k-средних с k=target,
# чья стоимость на итерацию растёт как O(точки x k): при k в десятки тысяч
# один вызов уходит в минуты вместо долей секунды и на практике выглядит как
# зависшее приложение (issue "приложение работает нестабильно, зависает").
# Тот же класс проблемы уже решён для того же файла в courtyard_design.py
# (MAX_DESIGN_AREA_M2) и для генератора по сетке в greenery_generator.py
# (MAX_GENERATED_TREES=400/MAX_GENERATED_BUSH_CLUSTERS=60, те же числа ниже
# для согласованности) -- здесь тот же приём, только для одной зоны, а не
# для всего дизайна: сверх этой границы зона получает столько объектов,
# сколько уместилось при разумной плотности, а не по одному на каждый
# квадратный метр гигантской территории.
MAX_OBJECTS_PER_ZONE = 400
MAX_GROUPS_PER_ZONE = 60

# Верхняя граница новой посадки на ВЕСЬ участок: потолок на зону не спасает,
# когда зон сотни (05_klykova_avenue, 815 зон -- 54 тыс. объектов; газон,
# проверка норм, браузер и DXF на таком объёме тормозили). Для сравнения: в
# реальном проекте 02_peschany_pereulok на 48 га -- ~2,5 тыс. посадок.
MAX_NEW_OBJECTS_PER_SITE = 10_000


def _polygon_of(zone: GeometricZone) -> Polygon:
    return Polygon([(p.x, p.z) for p in zone.polygon])


# Разброс масштаба деревьев (доля): в свободных посадках (рощи, разброс) --
# заметный, в регулярных (ряды, аллеи, сетки) -- небольшой, иначе теряется
# строгость рисунка.
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
        # Сотни одинаковых деревьев одного размера и одного поворота сразу
        # выдают клонов. Поворот ряда дереву не нужен (у кроны нет "лица"),
        # размер -- с разбросом, как у реального посадочного материала.
        # Детерминированно: сид -- ключ и место объекта (random.Random хеширует
        # строку через sha512, от PYTHONHASHSEED не зависит).
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
    """Кандидаты, не занятые уже стоящими объектами. points_in_area отбирает
    точки только по зонам ограничений: на участке с сотнями существующих
    деревьев (13_kharkovsky_proezd -- 287) центр рощи или точка заливки
    почти всегда попадали вплотную к старому дереву, вся группа отклонялась
    и роща не вставала вовсе, хотя рядом было место. Порода здесь не
    учитывается (область по таблице) -- её отступ проверяет is_free при
    посадке каждого вида."""
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
        # building_ring: зона -- узкая полоса-бублик вокруг здания
        # (building_border из zone_partitioning.py). Линия через центроид
        # (centerline_of/rows_of) срезала бы такую зону ХОРДОЙ по прямой,
        # а не обходила здание по кругу -- визуально не "кольцо", а
        # случайный отрезок внутри кольца. Собственный контур полигона --
        # и есть то самое кольцо.
        row_lines = [poly.exterior]
    elif spec.line_shape == "concentric":
        # concentric_rings: НЕСКОЛЬКО вложенных колец вокруг центроида зоны
        # (в отличие от line_shape="ring" выше -- там ровно одно кольцо по
        # контуру САМОЙ зоны). Своя ветка до проверки zone.kind ==
        # "open_area", т.к. используется собственная функция вместо rows_of.
        row_lines = concentric_rings_of(poly, spec.ring_spacing_m)
        if len(row_lines) > MAX_ROWS_PER_ZONE:
            row_lines = row_lines[:MAX_ROWS_PER_ZONE]  # ближайшие к центру кольца, не случайные
    elif zone.kind == "open_area":
        # open_area -- произвольной ширины двумерное пятно
        # (zone_partitioning.py: "всё, что осталось: под площадные
        # паттерны"), а не узкая полоса постоянной ширины вроде
        # building_border/path_corridor/site_edge. Одна линия через
        # середину покрывала только ряд посередине зоны, оставляя
        # остальную ширину открытой площади пустой -- реальная находка
        # ручного тестирования ("не заполняет всё пространство") на
        # linear-паттернах (flowing_rows/diagonal_rows), которые retrieval
        # назначает open_area наравне с площадными. rows_of() даёт столько
        # параллельных рядов, сколько уместится по ширине зоны.
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        row_lines = rows_of(poly, OPEN_AREA_ROW_SPACING_M, angle_offset_deg=angle)
        if len(row_lines) > MAX_ROWS_PER_ZONE:
            # Равномерная прорезка, а не первые MAX_ROWS_PER_ZONE -- rows_of
            # выдаёт ряды по порядку поперёк всей ширины зоны, взять только
            # первые N означало бы покрыть рядами одну сторону гигантской
            # зоны, оставив другую совсем пустой.
            step = len(row_lines) / MAX_ROWS_PER_ZONE
            row_lines = [row_lines[round(i * step)] for i in range(MAX_ROWS_PER_ZONE)]
    else:
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        centerline = centerline_of(poly, angle_offset_deg=angle)
        row_lines = [centerline] if centerline is not None else []

    if spec.line_shape == "wavy" and spec.wave_amplitude_m > 0:
        # Амплитуда из корпуса (4 м у 10_stary_gay) может быть шире самой
        # зоны (path_corridor -- полоса ~3 м) -- тогда волна почти всё время
        # уходила бы за пределы зоны и после обрезки по контуру рассыпалась
        # бы на мелкие несвязные обрывки у каждого пересечения нуля синуса.
        # Урезаем амплитуду под собственную ширину (короткую сторону)
        # каждой конкретной зоны -- на открытой достаточно широкой зоне
        # (open_area) применяется полная документированная амплитуда.
        short_side = min(s[2] for s in rect_sides(poly))
        amplitude = min(spec.wave_amplitude_m, short_side * 0.35)
        wavy = [wavy_line(line, amplitude, spec.wave_length_m) for line in row_lines]
        row_lines = [w.intersection(poly) for w in wavy if w is not None]

    if not row_lines:
        return []
    offsets = (0.0,) if spec.double_row_offset_m <= 0 else (0.0, spec.double_row_offset_m)
    tree_step = spec.tree_step_m if spec.tree_step_m is not None else LINEAR_BUSH_STEP_M * LINEAR_TREE_STEP_FACTOR
    # Тот же потолок MAX_OBJECTS_PER_ZONE, что у площадной заливки, -- через
    # шаг, а не обрезкой рядов: 10 рядов по многие сотни метров на
    # гигантской open_area давали до 6000 кустов на ОДНУ зону (02_peschany:
    # 54 тыс. новых объектов на участок -- тормозили браузер, газон, DXF).
    # Обрезка оставила бы засаженными только первые ряды; больший шаг
    # сохраняет все ряды по всей зоне. Узкие полосы (изгородь вдоль дорожки)
    # в потолок укладываются и остаются с шагом 1.4 м.
    total_length = sum(line.length for line in row_lines)
    tree_step = max(tree_step, total_length / MAX_OBJECTS_PER_ZONE)
    bush_step = max(LINEAR_BUSH_STEP_M, total_length * len(offsets) / MAX_OBJECTS_PER_ZONE)
    objects: list[SceneObject] = []
    bush_counter, tree_counter = [0], [0]
    for row_centerline in row_lines:
        for line in lines_of(row_centerline):
            # Деревья -- ПЕРЕД кустами на той же линии (offset=0.0 у обоих).
            # Куст с шагом LINEAR_BUSH_STEP_M=1.4 м занимает буквально каждую
            # точку центральной линии -- ближайший куст к любой другой точке
            # той же линии всегда ближе OBJECT_CLEARANCE_M=1.0 м. Если сажать
            # кусты первыми (как было раньше), ни одна точка-кандидат для
            # дерева на этой же линии не проходит is_free -- деревья не
            # появлялись ВООБЩЕ ни на одном линейном паттерне (issue:
            # "деревья вообще не появляются", реальный баг с
            # 01_single_building/02_courtyard). Деревья реже (шаг в
            # LINEAR_TREE_STEP_FACTOR=8.5 раз больше, если паттерн не задал
            # свой tree_step_m) и сами по себе не мешают кустам занять
            # остальную линию после них.
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
            # triangular_grid_fill: решётка НА целевом шаге, без прореживания
            # pick_spread -- pick_spread даёт случайный на вид равномерный
            # разброс (poisson_scatter_fill/generic_fill), а треугольной
            # сетке (квинкункс) нужен видимый регулярный узор -- сама
            # гексагональная решётка Placer.points_in_area и есть этот узор.
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
    """Полный проход Этапов 3-5 на одной сцене: зонирование -> retrieval и
    назначение паттернов -> детерминированная расстановка. Возвращает только
    НОВЫЕ объекты (как generate_trees/generate_bushes в greenery_generator.py
    -- вызывающий код сам решает, добавлять ли их в scene.objects) и список
    ZoneAssignment с provenance -- на нём в следующей итерации строится
    Этап 6 (отчёт "по аналогии с проектом X"), без переделки этой функции.
    С параметрами пользователя (стиль, предпочтительные виды) -- plan_site."""
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
    # usable_planting_area -- unary_union по всем forbidden/warning-зонам
    # участка, самая дорогая геометрическая операция во всём проходе (на
    # плотных реальных участках -- тысячи зон). partition_zones и
    # assign_patterns (через characterize_site) считали её независимо, т.е.
    # дважды за один и тот же запрос -- считаем один раз здесь и пробрасываем.
    usable = usable_planting_area(scene)
    zones = partition_zones(scene, usable=usable)
    characteristics = characterize_site(scene, usable=usable)
    assignments = assign_patterns(scene, zones, k=k, characteristics=characteristics, style=style)
    placer = Placer(scene)

    # Виды -- по нормативному ассортименту для типа территории, форме,
    # нужной паттерну, и без инвазивных (species_selection.py), а не весь
    # переданный каталог по кругу. Один набор на пару (вид зоны, паттерн) на
    # всём участке -- изгородь вдоль всех дорожек из одного вида.
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
