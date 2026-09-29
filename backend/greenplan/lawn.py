"""
Газон GreenPlan -- "травянистые покрытия" из ТЗ. Газон -- площадь, а не
объекты: в проекте его считают в м² (ведомость элементов озеленения, ГОСТ
21.508-2020, форма 9: "Газон партерный -- 1240 м²"), поэтому здесь не
раскладываются плитки, как у генератора по сетке, а считаются полигоны.

Где газон:
    разрешённые зоны сцены (GRASS/LAWN/ГАЗОН из плана покрытий и вычисленная
    парсером "открытая земля"; нет ни одной -- весь участок)
  минус твёрдые покрытия и сооружения (здания, проезды, дорожки, детские
    площадки, парковки и прочие custom-зоны, охраняемые зоны);
  минус клумбы кустарника (новые и существующие кусты с радиусом кроны,
    ряды сливаются в сплошную полосу).

Над инженерными сетями газон допускается: таблица 9.1 СП 42.13330 (и 3.6.1
743-ПП) нормирует расстояния только для деревьев и кустарников. Под кроной
деревьев газон тоже есть -- так его и проектируют; приствольные лунки не
вычитаются (это деталь рабочей документации).

Статус: часть на существующем газоне из исходного плана -- existing
(сохраняется), остальное -- new (устройство газона на открытой земле: только
эта площадь идёт в ведомость и в объём посева). Вид -- "Газон обыкновенный",
посев из устойчивой травосмеси (ППМ 515-ПП, табл. 4).
"""

from __future__ import annotations

from collections import defaultdict

import shapely
from shapely.geometry import MultiPoint, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.plant_catalog import CatalogItem
from core.schemas import LawnArea, Point2, RestrictionZone, Scene
from core.shapes import polygon_from_points

LAWN_KIND = "Газон обыкновенный"

# Зоны, на которых газона не бывает: покрытия и сооружения. Сети (газ,
# канализация, вода, кабели, теплосеть, ЛЭП) сюда намеренно не входят.
HARD_SURFACE_TYPES = frozenset({"building", "transformer", "road", "pedestrian_path", "playground_zone", "custom"})

# Полоса газона уже этого не укладывается (обрезки между клумбой и дорожкой),
# кусок меньше MIN_LAWN_AREA_SQM -- шум геометрии, а не газон.
MIN_LAWN_WIDTH_M = 0.5
MIN_LAWN_AREA_SQM = 5.0
# Кусты одного ряда (шаг 1.4 м) сливаются в сплошную клумбу, а не остаются
# отдельными кружками с газоном между ними.
SHRUB_BED_MERGE_M = 0.6
DEFAULT_SHRUB_RADIUS_M = 0.5
# Упрощение контура: сантиметровая точность газону не нужна, а вершины
# реальных контуров покрытий идут в JSON сцены и на отрисовку.
SIMPLIFY_TOLERANCE_M = 0.25

# Существующий газон -- разрешённые зоны со слоёв плана покрытий
# (parser/dxf_parsing/rules.py: GRASS, LAWN, ГАЗОН; сюда же попадает наш
# экспорт NEW_LAWN). Разрешённая зона со слоя GROUND и вычисленная парсером
# "открытая земля" -- это земля без покрытия: газон на ней -- новый.
LAWN_COVER_LAYER_KEYWORDS = ("GRASS", "LAWN", "ГАЗОН")


def _poly(points) -> BaseGeometry | None:
    return polygon_from_points(points)


def _is_hard_surface(zone: RestrictionZone) -> bool:
    if zone.severity == "allowed":
        return False
    if zone.type == "protected_zone":
        return True
    return zone.type in HARD_SURFACE_TYPES


def _shrub_beds(scene: Scene, catalog: dict[str, CatalogItem]) -> BaseGeometry | None:
    """Клумбы кустарника: круг кроны вокруг каждого куста, соседние кусты
    ряда слиты в одну клумбу (замыкание на SHRUB_BED_MERGE_M). Кусты
    сгруппированы по радиусу и буферизуются одним MultiPoint на группу --
    на участках с десятками тысяч кустов это втрое быстрее, чем объединять
    каждый круг по отдельности."""
    by_radius: dict[float, list[tuple[float, float]]] = defaultdict(list)
    for obj in scene.objects:
        if obj.type not in ("bush", "hedge_segment"):
            continue
        item = catalog.get(obj.metadata.get("catalogId"))
        radius = (item.dimensions.radius if item and item.dimensions.radius else None) or DEFAULT_SHRUB_RADIUS_M
        if obj.type == "hedge_segment" and item and item.dimensions.width:
            radius = max(radius, item.dimensions.width / 2)
        by_radius[round(radius, 2)].append((obj.position.x, obj.position.z))
    if not by_radius:
        return None
    # Расширение на радиус + запас слияния, затем сужение на запас -- то же,
    # что замыкание объединения кругов, одной операцией на группу.
    grown = [MultiPoint(points).buffer(radius + SHRUB_BED_MERGE_M, quad_segs=3) for radius, points in by_radius.items()]
    return shapely.union_all(grown).buffer(-SHRUB_BED_MERGE_M, quad_segs=3)


def _to_areas(geom: BaseGeometry, status: str, start_index: int) -> list[LawnArea]:
    areas = []
    parts = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    for part in parts:
        if part.area < MIN_LAWN_AREA_SQM:
            continue
        areas.append(
            LawnArea(
                id=f"lawn_{status}_{start_index + len(areas) + 1:03d}",
                polygon=[Point2(x=x, z=z) for x, z in list(part.exterior.coords)[:-1]],
                holes=[[Point2(x=x, z=z) for x, z in list(ring.coords)[:-1]] for ring in part.interiors],
                area_sqm=round(part.area, 1),
                status=status,
                kind=LAWN_KIND,
            )
        )
    return areas


# Сетка привязки для повтора операции, если GEOS не справился с почти
# совпадающими рёбрами (раскрытие "mitre" на длинных параллельных полосах --
# бульвар, дорога с тротуарами): 1 см -- заметно меньше точности газона.
_SNAP_GRID_M = 0.01


def _robust(operation: str, a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    """a.<operation>(b), а при TopologyException -- то же после починки и
    привязки обеих геометрий к сетке _SNAP_GRID_M, вместо падения запроса."""
    try:
        return getattr(a, operation)(b)
    except shapely.errors.GEOSException:
        a2 = shapely.set_precision(shapely.make_valid(a), _SNAP_GRID_M)
        b2 = shapely.set_precision(shapely.make_valid(b), _SNAP_GRID_M)
        return getattr(a2, operation)(b2)


def plan_lawns(scene: Scene, catalog: dict[str, CatalogItem]) -> list[LawnArea]:
    """Газон на сцене -- после расстановки посадок (кусты уже стоят)."""
    if scene.boundary is None:
        return []
    boundary = _poly(scene.boundary.polygon)
    if boundary is None:
        return []

    allowed = [z for z in scene.restrictions if z.severity == "allowed" and z.type != "selection"]
    allowed_geoms = [g for g in (_poly(z.polygon) for z in allowed) if g is not None]
    base = _robust("intersection", unary_union(allowed_geoms), boundary) if allowed_geoms else boundary

    blockers = [g for g in (_poly(z.polygon) for z in scene.restrictions if _is_hard_surface(z)) if g is not None]
    beds = _shrub_beds(scene, catalog)
    if beds is not None:
        blockers.append(beds)
    lawn = _robust("difference", base, unary_union(blockers)) if blockers else base
    # Раскрытие убирает полосы уже MIN_LAWN_WIDTH_M, затем упрощение контура.
    # Углы -- "mitre": круглые скругляли бы каждый угол участка фаской, и
    # упрощение потом срезало бы по фаске весь край (-0,3% площади квадрата).
    half = MIN_LAWN_WIDTH_M / 2
    lawn = _robust("intersection", lawn.buffer(-half, join_style="mitre").buffer(half, join_style="mitre"), lawn)
    lawn = lawn.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    if lawn.is_empty:
        return []

    existing_cover = [
        g
        for z, g in ((z, _poly(z.polygon)) for z in allowed)
        if g is not None and any(k in z.name.upper() for k in LAWN_COVER_LAYER_KEYWORDS)
    ]
    if existing_cover:
        cover = unary_union(existing_cover)
        existing, new = _robust("intersection", lawn, cover), _robust("difference", lawn, cover)
    else:
        existing, new = Polygon(), lawn

    areas = _to_areas(new, "new", 0) if not new.is_empty else []
    if not existing.is_empty:
        areas += _to_areas(existing, "existing", 0)
    return areas


def existing_lawns(scene: Scene, catalog: dict[str, CatalogItem]) -> list[LawnArea]:
    """Сохраняемый газон исходного чертежа: газон со слоёв плана покрытий за
    вычетом покрытий, сооружений и клумб кустарника. Отдаётся сразу при
    загрузке чертежа, чтобы газон был виден до GreenPlan; новый газон на
    открытой земле предлагает только GreenPlan."""
    return [area for area in plan_lawns(scene, catalog) if area.status == "existing"]


def lawn_totals(lawns: list[LawnArea]) -> tuple[float, float]:
    """(новый газон, м²; сохраняемый существующий, м²)."""
    new = sum(a.area_sqm for a in lawns if a.status == "new")
    existing = sum(a.area_sqm for a in lawns if a.status == "existing")
    return round(new, 1), round(existing, 1)
