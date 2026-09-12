"""
Экспорт итогового плана обратно в DXF (ТЗ: "итоговый план должен
экспортироваться обратно в формат DXF") -- зеркальное отражение
parser/parse_dxf.py: тот читает слои DXF в JSON-сцену, этот пишет JSON-сцену
обратно слоями DXF, по возможности теми же именами, чтобы файл можно было
не глядя открыть в AutoCAD/nanoCAD и узнать план, а при желании -- скормить
обратно нашему же парсеру.

Координаты пишутся как есть, в метрах (сцена уже в метрах и уже
отцентрирована парсером -- см. parse_dxf.Transform): не пытаемся восстановить
исходную систему координат/масштаб DXF-файла, потому что после ручных правок,
правки текстом и генерации это было бы фикцией -- реального "исходного listing"
для отредактированного плана не существует, есть только текущая сцена.
$INSUNITS выставлен в метры (6), чтобы масштаб при открытии не терялся.

Слои:
* здания и зоны ограничений -- по zone.name/сохранённому sourceLayer, когда
  он есть (тогда parse_dxf.py при повторном импорте узнает тот же тип по
  той же подстроке в имени слоя) или по типу зоны, когда объект создан не из
  DXF (например define_zone в llm_editor.py);
* точечные посадки и МАФ -- по типу объекта заглавными буквами (TREE, BUSH,
  LAMP, BENCH, ...) -- ровно те подстроки, что parser.POINT_LAYER_RULES уже
  ищет для базовых 6 типов, так что для них выходит настоящий round-trip;
  видов, которых на входе не было (секция изгороди, сегмент дорожки, газон,
  цветник, урна, фонтан), parser пока не знает -- для них слой всё равно
  осмысленный и различимый в CAD, просто повторный импорт не считает его
  особым типом, а не то чтобы терял данные молча.
"""

from __future__ import annotations

import math
from typing import Optional

import ezdxf
from plant_catalog import CatalogItem, catalog_by_id
from schemas import Scene, SceneObject

DXF_VERSION = "R2010"  # широко поддерживаемая версия, читается почти любым CAD
POINT_MARKER_RADIUS_M = 0.15  # видимый маркер под POINT-сущностью (в большинстве CAD точки не отображаются)

# Цвета по AutoCAD Color Index -- красный/жёлтый/зелёный совпадают с той же
# логикой severity, что и в 3D-редакторе (frontend/src/scene/RestrictionZones.tsx).
_ACI_BY_SEVERITY = {"forbidden": 1, "warning": 2, "allowed": 3}
_ACI_BUILDING = 8  # серый
_ACI_BOUNDARY = 7  # белый/чёрный, в зависимости от темы CAD
_ACI_ENTRANCE = 5  # синий
_ACI_DEFAULT_OBJECT = 3  # зелёный -- по умолчанию для растений; МАФ переопределяют ниже
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


# Тип объекта -> имя слоя, когда своего sourceLayer нет (объект создан
# генератором/LLM, а не пришёл из исходного DXF). По умолчанию это просто
# obj.type.upper(), НО два типа так делать нельзя: "PATH_SEGMENT" содержит
# подстроку "PATH", а "LAWN_PATCH" -- подстроку "LAWN", и
# parser.POLYGON_RULES ищет ровно эти подстроки для зон ограничений.
# Экспортированный план с сотнями плиток дорожки/газона на таких слоях при
# повторном импорте тем же parse_dxf.py превращался бы в сотни лишних
# зон-ограничений вместо десятка настоящих -- проверено, именно так и
# происходило до этого исправления.
_SAFE_TYPE_LAYER = {
    "path_segment": "PAVING",
    "lawn_patch": "TURF",
}


def _object_layer(obj: SceneObject) -> str:
    source = obj.metadata.get("sourceLayer")
    if isinstance(source, str) and source:
        return _safe_layer_name(source, obj.type.upper())
    layer_name = _SAFE_TYPE_LAYER.get(obj.type, obj.type.upper())
    return _safe_layer_name(layer_name, "OBJECT")


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
    # Тот же поворот, что и в планировщике (courtyard_design._footprint):
    # ux, uz = cosθ, -sinθ -- ось X объекта; vx, vz = sinθ, cosθ -- ось Z.
    # DXF Y здесь -- это Z сцены, но сама формула должна быть побуквенно той
    # же, иначе прямоугольные объекты (мощение/изгородь/клумба) экспортировались
    # бы зеркально повёрнутыми относительно того, как они на самом деле стоят
    # в сцене (была именно эта ошибка -- знаки не совпадали с courtyard_design.py).
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
        if len(zone.polygon) < 3:
            continue
        layer = _zone_layer(zone.type, zone.name)
        _ensure_layer(doc, layer, _ACI_BY_SEVERITY.get(zone.severity, 7))
        points = [(p.x, p.z) for p in zone.polygon]
        pl = msp.add_lwpolyline(points, close=True, dxfattribs={"layer": layer})
        # message/severity/minDistance -- не геометрия, но терять их при
        # экспорте незачем: XDATA переживает открытие в любом CAD и наш же
        # повторный импорт (который их всё равно заново вычислит по слою,
        # а не читает XDATA) не портит.
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


def _write_point_object(doc, msp, obj: SceneObject, catalog: dict[str, CatalogItem]) -> None:
    layer = _object_layer(obj)
    color = _ACI_BY_OBJECT_TYPE.get(obj.type, _ACI_DEFAULT_OBJECT)
    _ensure_layer(doc, layer, color)

    item = catalog.get(obj.metadata.get("catalogId"))
    x, z = obj.position.x, obj.position.z
    footprint = _footprint(item, x, z, obj.rotation)
    if footprint is not None:
        # Мощение/изгородь/газон/клумба -- реальный габарит, а не точка:
        # ландшафтному архитектору, открывшему план в CAD, нужна ширина
        # дорожки, а не просто метка "здесь дорожка".
        msp.add_lwpolyline(footprint, close=True, dxfattribs={"layer": layer})
        return

    # Обычная точечная посадка/МАФ. Отдельный маленький CIRCLE поверх POINT --
    # многие CAD-просмотрщики по умолчанию не показывают "голые" точки
    # никаким видимым маркером (размер точки -- настройка вьюпорта, а не
    # свойство файла), а без видимого маркера план выглядел бы пустым.
    msp.add_point((x, z, 0.0), dxfattribs={"layer": layer})
    radius = (item.dimensions.radius if item and item.dimensions.radius else None) or POINT_MARKER_RADIUS_M
    msp.add_circle((x, z), radius=min(radius, 1.5), dxfattribs={"layer": layer})


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

    _write_facade(doc, msp, scene.windows, "WINDOWS", 5)
    _write_facade(doc, msp, scene.canopies, "CANOPIES", 6)

    return doc
