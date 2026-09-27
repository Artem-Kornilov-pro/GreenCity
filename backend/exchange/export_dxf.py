"""
Экспорт сцены в DXF -- обратная сторона parser/parse_dxf.py: сцена пишется
слоями DXF, по возможности с теми же именами, чтобы план узнавался в
AutoCAD/nanoCAD и читался нашим же парсером.

Если исходный чертёж сохранён, экспорт идёт поверх него (dxf_overlay.py).
Этот модуль -- геометрия сущностей и запасной полный экспорт: координаты
сцены в метрах от центра участка, $INSUNITS = 6 (метры).

Слои:
* здания и зоны ограничений -- по sourceLayer или имени зоны, если они есть,
  иначе по типу зоны;
* исходные точечные объекты -- на свой слой из чертежа;
* новое -- на слои результата: NEW_* (GreenPlan) и USER_* (правки
  пользователя).
"""

from __future__ import annotations

import math
from typing import Optional

import ezdxf
from ezdxf.lldxf import const

from core.plant_catalog import CatalogItem, catalog_by_id
from core.schemas import Scene, SceneObject

DXF_VERSION = "R2010"  # широко поддерживаемая версия, читается почти любым CAD
POINT_MARKER_RADIUS_M = 0.15  # видимый маркер под POINT-сущностью (в большинстве CAD точки не отображаются)

# Цвета по AutoCAD Color Index -- красный/жёлтый/зелёный совпадают с той же
# логикой severity, что и в 3D-редакторе (frontend/src/scene/RestrictionZones.tsx).
_ACI_BY_SEVERITY = {"forbidden": 1, "warning": 2, "allowed": 3}
_ACI_BUILDING = 8  # серый
_ACI_BOUNDARY = 7  # белый/чёрный, в зависимости от темы CAD
_ACI_ENTRANCE = 5  # синий
_ACI_DEFAULT_OBJECT = 3  # зелёный -- по умолчанию для растений; МАФ переопределяют ниже
_ACI_LAWN = 82  # светло-зелёный -- заливка газона, отличается от зелёных контуров посадок
LAWN_LAYER = "NEW_LAWN"
_ACI_BY_OBJECT_TYPE = {
    "lamp": 2,
    "bench": 42,
    "trash": 8,
    "fountain": 4,
    "path_segment": 9,
    "hedge_segment": 94,
}


def _safe_layer_name(name: str, fallback: str) -> str:
    """DXF-слой не терпит <>/\\":;?*|,=` и пустое имя; кириллица и пробелы
    формально допустимы в современном DXF, но лучше не рисковать с более
    старыми читалками -- транслитерацию не делаем (это исказило бы узнаваемое
    имя), а просто убираем недопустимые символы и подставляем запасное имя,
    если после этого ничего не осталось."""
    cleaned = "".join(c for c in name if c not in '<>/\\":;?*|,=`').strip()
    return (cleaned or fallback)[:63]


# Тип объекта -> слой, если своего sourceLayer нет. По умолчанию
# obj.type.upper(), но PATH_SEGMENT и LAWN_PATCH содержат подстроки PATH и
# LAWN, по которым парсер ищет зоны, -- при повторном импорте сотни плиток
# стали бы зонами ограничений.
_SAFE_TYPE_LAYER = {
    "path_segment": "PAVING",
    "lawn_patch": "TURF",
}


# Слои результата: посадка и благоустройство GreenPlan -- NEW_*, правки
# пользователя -- USER_*, места убранных или сдвинутых исходных объектов --
# USER_REMOVED (исходный слой не меняется).
NEW_PREFIX = "NEW_"
USER_PREFIX = "USER_"
USER_REMOVED_LAYER = "USER_REMOVED"
USER_ZONES_LAYER = "USER_ZONES"


def is_source_object(obj: SceneObject) -> bool:
    """Объект пришёл из исходного чертежа (у таких парсер хранит слой)."""
    source = obj.metadata.get("sourceLayer")
    return isinstance(source, str) and bool(source) and not obj.metadata.get("generated")


def result_layer(obj: SceneObject) -> str:
    """Слой результата для объекта, которого нет в исходном чертеже."""
    layer_name = _SAFE_TYPE_LAYER.get(obj.type, obj.type.upper())
    prefix = NEW_PREFIX if obj.metadata.get("generated") else USER_PREFIX
    return _safe_layer_name(f"{prefix}{layer_name}", f"{prefix}OBJECT")


def _object_layer(obj: SceneObject) -> str:
    # Исходный объект -- на свой слой из чертежа; GreenPlan
    # (metadata.generated) -- NEW_*, правки пользователя -- USER_*.
    if is_source_object(obj):
        return _safe_layer_name(obj.metadata["sourceLayer"], obj.type.upper())
    return result_layer(obj)


def _zone_layer(zone_type: str, zone_name: str) -> str:
    if zone_name:
        return _safe_layer_name(zone_name, zone_type.upper())
    return _safe_layer_name(zone_type.upper(), "ZONE")


def _ensure_layer(doc, name: str, color: int) -> None:
    if name not in doc.layers:
        doc.layers.add(name, color=color)


def _footprint(item: Optional[CatalogItem], x: float, z: float, rotation_rad: float):
    """Вершины (x, y) прямоугольного следа объекта в плоскости XY DXF (Y
    DXF = Z сцены), если у вида в каталоге есть width/depth (мощение,
    изгородь, клумба, газон) -- иначе None: точечный объект рисуется без
    контура, только маркером (см. _write_point_object)."""
    if item is None or not (item.dimensions.width and item.dimensions.depth):
        return None
    hw, hd = item.dimensions.width / 2, item.dimensions.depth / 2
    # Поворот -- как в планировщике (courtyard_design._footprint): ось X
    # объекта (cosθ, -sinθ), ось Z (sinθ, cosθ); Y в DXF -- это Z сцены.
    # Другие знаки зеркально повернули бы прямоугольные объекты.
    ux, uz = math.cos(rotation_rad), -math.sin(rotation_rad)
    vx, vz = math.sin(rotation_rad), math.cos(rotation_rad)
    return [
        (x + ux * hw + vx * hd, z + uz * hw + vz * hd),
        (x - ux * hw + vx * hd, z - uz * hw + vz * hd),
        (x - ux * hw - vx * hd, z - uz * hw - vz * hd),
        (x + ux * hw - vx * hd, z + uz * hw - vz * hd),
    ]


def _write_boundary(doc, msp, scene: Scene) -> None:
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return
    layer = _safe_layer_name(scene.boundary.sourceLayer, "BOUNDARY")
    _ensure_layer(doc, layer, _ACI_BOUNDARY)
    points = [(p.x, p.z) for p in scene.boundary.polygon]
    msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})


def _write_zones(doc, msp, scene: Scene) -> None:
    for zone in scene.restrictions:
        _write_zone(doc, msp, zone)


def _write_zone(doc, msp, zone, layer: Optional[str] = None) -> None:
    if len(zone.polygon) < 3:
        return
    layer = layer or _zone_layer(zone.type, zone.name)
    _ensure_layer(doc, layer, _ACI_BY_SEVERITY.get(zone.severity, 7))
    points = [(p.x, p.z) for p in zone.polygon]
    pl = msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})
    # message, severity и minDistance -- в XDATA: они переживают открытие в
    # CAD, а парсер при повторном импорте их всё равно вычисляет по слою.
    pl.set_xdata(
        "GREENCITY",
        [
            (1000, zone.severity),
            (1000, zone.message[:255]),
            (1040, float(zone.minDistance)),
        ],
    )


def _write_buildings(doc, msp, scene: Scene) -> None:
    for obj in scene.objects:
        if obj.type != "building":
            continue
        footprint = obj.metadata.get("footprint") or []
        if len(footprint) < 3:
            continue
        layer = _safe_layer_name(obj.metadata.get("sourceLayer") or "", "BUILDING_FOOTPRINT")
        _ensure_layer(doc, layer, _ACI_BUILDING)
        points = [(p["x"], p["z"]) for p in footprint]
        msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})

        name = obj.metadata.get("name") or obj.id
        height = obj.metadata.get("height")
        label = f"{name} h={height}m" if height is not None else str(name)
        label_layer = _safe_layer_name("LABEL", "LABEL")
        _ensure_layer(doc, label_layer, 7)
        cx = sum(p["x"] for p in footprint) / len(footprint)
        cz = sum(p["z"] for p in footprint) / len(footprint)
        text = msp.add_text(label, dxfattribs={"layer": label_layer, "height": 1.2})
        text.dxf.insert = (cx, cz)


def _write_point_object(doc, msp, obj: SceneObject, catalog: dict[str, CatalogItem], layer: Optional[str] = None) -> list:
    """Сущности объекта (контур или точка с кружком). Возвращает их -- чтобы
    вызывающий мог повесить на них XDATA с объяснением посадки."""
    layer = layer or _object_layer(obj)
    color = _ACI_BY_OBJECT_TYPE.get(obj.type, _ACI_DEFAULT_OBJECT)
    _ensure_layer(doc, layer, color)

    item = catalog.get(obj.metadata.get("catalogId"))
    x, z = obj.position.x, obj.position.z
    footprint = _footprint(item, x, z, obj.rotation)
    if footprint is not None:
        # Мощение, изгородь, газон и клумба -- реальным габаритом: в CAD
        # нужна ширина дорожки, а не метка.
        return [msp.add_lwpolyline(footprint, close=True, dxfattribs={"layer": layer})]

    # Точечный объект: POINT и маленький CIRCLE -- голые точки многие
    # просмотрщики не показывают.
    point = msp.add_point((x, z, 0.0), dxfattribs={"layer": layer})
    radius = (item.dimensions.radius if item and item.dimensions.radius else None) or POINT_MARKER_RADIUS_M
    return [point, msp.add_circle((x, z), radius=min(radius, 1.5), dxfattribs={"layer": layer})]


def _write_entrances(doc, msp, scene: Scene) -> None:
    layer = _safe_layer_name("ENTRANCES", "ENTRANCES")
    added = False
    for obj in scene.objects:
        if obj.type != "entrance":
            continue
        if not added:
            _ensure_layer(doc, layer, _ACI_ENTRANCE)
            added = True
        msp.add_circle((obj.position.x, obj.position.z), radius=POINT_MARKER_RADIUS_M, dxfattribs={"layer": layer})


def _write_facade(doc, msp, quads: list, layer_name: str, color: int) -> None:
    if not quads:
        return
    _ensure_layer(doc, layer_name, color)
    for quad in quads:
        if len(quad) != 4:
            continue
        verts = [(p.x, p.z, p.y) for p in quad]  # DXF Z -- это высота (сцена: y)
        msp.add_3dface(verts, dxfattribs={"layer": layer_name})


def _write_curbs(doc, msp, polylines: list, layer_name: str, color: int) -> None:
    if not polylines:
        return
    _ensure_layer(doc, layer_name, color)
    for pl in polylines:
        if len(pl) < 2:
            continue
        points = [(p.x, p.z) for p in pl]
        # Бордюр -- открытая линия (не замкнутый контур), в отличие от границ/зон.
        msp.add_lwpolyline(points, close=False, dxfattribs={"layer": layer_name})


def _write_lawns(doc, msp, scene: Scene) -> None:
    """Новый газон GreenPlan -- заливкой HATCH на слое NEW_LAWN, клумбы --
    дырками в ней. Существующий газон уже записан своей зоной. При повторной
    загрузке слой читается как разрешённая зона газона (вместе с клумбами --
    это безвредно: кусты загрузятся точками, и GreenPlan снова вычтет их)."""
    lawns = [a for a in scene.lawns if a.status == "new"]
    if not lawns:
        return
    _ensure_layer(doc, LAWN_LAYER, _ACI_LAWN)
    for lawn in lawns:
        hatch = msp.add_hatch(color=_ACI_LAWN, dxfattribs={"layer": LAWN_LAYER})
        hatch.paths.add_polyline_path([(p.x, p.z) for p in lawn.polygon], is_closed=True, flags=const.BOUNDARY_PATH_EXTERNAL)
        for hole in lawn.holes:
            hatch.paths.add_polyline_path([(p.x, p.z) for p in hole], is_closed=True, flags=const.BOUNDARY_PATH_OUTERMOST)


def scene_to_dxf(scene: Scene) -> ezdxf.document.Drawing:
    """JSON-сцена -> открытый ezdxf-документ, готовый к doc.write(...)."""
    doc = ezdxf.new(DXF_VERSION, setup=False)
    doc.header["$INSUNITS"] = 6  # метры -- сцена уже в метрах (см. докстринг модуля)
    if "GREENCITY" not in doc.appids:
        doc.appids.new("GREENCITY")  # для set_xdata на зонах ограничений (_write_zones)
    msp = doc.modelspace()

    _write_boundary(doc, msp, scene)
    _write_zones(doc, msp, scene)
    _write_buildings(doc, msp, scene)
    _write_entrances(doc, msp, scene)

    catalog = catalog_by_id()
    for obj in scene.objects:
        if obj.type in ("building", "entrance"):
            continue  # уже записаны выше своей геометрией
        _write_point_object(doc, msp, obj, catalog)

    _write_lawns(doc, msp, scene)
    _write_facade(doc, msp, scene.windows, "WINDOWS", 5)
    _write_facade(doc, msp, scene.canopies, "CANOPIES", 6)
    _write_curbs(doc, msp, scene.curbs, "CURBS", 9)

    return doc
