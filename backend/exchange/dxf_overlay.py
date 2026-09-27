"""
Экспорт ПОВЕРХ исходного чертежа (ТЗ, "Что должен уметь сервис", п. 5 и 7):
исходные слои подосновы, коммуникаций и городской среды не изменяются, всё
новое -- на отдельных слоях, в системе координат исходника.

Как: исходный DXF (exchange/source_store.py) открывается и разбирается тем
же парсером, что и при загрузке, -- это база. Сцена из редактора
сравнивается с базой по id объектов и зон (парсер детерминирован: тот же
файл даёт те же id), и в документ дописываются только отличия:

* посадка и благоустройство GreenPlan -- NEW_* (NEW_TREE, NEW_BUSH,
  NEW_LAWN, NEW_PATHS, ...);
* объекты, добавленные пользователем (ИИ-ассистент, панель «Добавить
  объект»), и новое место сдвинутых или изменённых исходных -- USER_*;
* места исходных объектов, которые пользователь удалил или сдвинул, --
  крестик на USER_REMOVED: сам исходный объект в своём слое остаётся как был;
* зоны, выделенные в редакторе (define_zone), -- USER_ZONES.

Геометрия пишется теми же функциями, что и полный экспорт
(exchange/export_dxf.py), в координатах сцены (метры от центра участка), и
затем переводится в координаты исходника обратным преобразованием парсера:
X = x / scale + origin.x, Y = z / scale + origin.y (parser/dxf_parsing/geometry.Transform).

На каждой сущности результата -- XDATA GREENCITY с id объекта: по нему
посадка находится в файле объяснений (greenplan/explanations.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ezdxf.lldxf import const
from ezdxf.math import Matrix44

from core.plant_catalog import catalog_by_id
from core.schemas import Scene, SceneObject
from exchange.dxf_parser import parse_dxf_doc
from exchange.export_dxf import (
    _ACI_BY_SEVERITY,
    _ACI_LAWN,
    LAWN_LAYER,
    USER_REMOVED_LAYER,
    USER_ZONES_LAYER,
    _ensure_layer,
    _safe_layer_name,
    _write_point_object,
    result_layer,
)
from greenplan.improvements import is_greenplan_zone

APPID = "GREENCITY"
POSITION_TOLERANCE_M = 0.01
ANGLE_TOLERANCE_RAD = 1e-3
REMOVED_MARK_HALF_M = 0.6
# Объекты, которые редактор не создаёт и не двигает: их в исходнике не трогаем.
_STRUCTURAL = {"building", "entrance"}


@dataclass
class OverlaySummary:
    layers: dict[str, int] = field(default_factory=dict)  # слой результата -> число объектов

    def count(self, layer: str) -> None:
        self.layers[layer] = self.layers.get(layer, 0) + 1


def _changed(old: SceneObject, new: SceneObject) -> bool:
    moved = math.hypot(old.position.x - new.position.x, old.position.z - new.position.z) > POSITION_TOLERANCE_M
    turned = abs(old.rotation - new.rotation) > ANGLE_TOLERANCE_RAD
    scaled = abs(old.scale - new.scale) > 1e-3
    kind = (old.type, old.metadata.get("catalogId"), old.metadata.get("species")) != (
        new.type,
        new.metadata.get("catalogId"),
        new.metadata.get("species"),
    )
    return moved or turned or scaled or kind


def _tag(entities, obj_id: str, *notes: str) -> None:
    data = [(1000, obj_id[:255])] + [(1000, n[:255]) for n in notes if n]
    for entity in entities:
        entity.set_xdata(APPID, data)


def _plant_notes(obj: SceneObject) -> tuple[str, ...]:
    meta = obj.metadata
    species = str(meta.get("species") or meta.get("label") or obj.type)
    if meta.get("generated"):
        origin = f"GreenPlan: {meta.get('pattern_id') or 'благоустройство'}"
        if meta.get("source_project"):
            origin += f", аналог {meta['source_project']}"
    else:
        origin = "правка пользователя"
    return species, origin


def overlay_scene(scene: Scene, doc) -> OverlaySummary:
    """Дописать в doc (исходный чертёж) слои результата по сцене scene.
    doc меняется на месте; исходные сущности не трогаются."""
    baseline = Scene.model_validate(parse_dxf_doc(doc))
    scale = baseline.meta.scale or 1.0
    ox, oy = float(baseline.meta.origin.get("x", 0.0)), float(baseline.meta.origin.get("y", 0.0))
    to_source = Matrix44.chain(Matrix44.scale(1 / scale, 1 / scale, 1 / scale), Matrix44.translate(ox, oy, 0))

    if APPID not in doc.appids:
        doc.appids.new(APPID)
    msp = doc.modelspace()
    catalog = catalog_by_id()
    summary = OverlaySummary()
    created = []

    base_objects = {o.id: o for o in baseline.objects}
    current_ids = {o.id for o in scene.objects}

    def removed_mark(obj: SceneObject, why: str) -> None:
        _ensure_layer(doc, USER_REMOVED_LAYER, 1)
        x, z, h = obj.position.x, obj.position.z, REMOVED_MARK_HALF_M
        lines = [
            msp.add_line((x - h, z - h), (x + h, z + h), dxfattribs={"layer": USER_REMOVED_LAYER}),
            msp.add_line((x - h, z + h), (x + h, z - h), dxfattribs={"layer": USER_REMOVED_LAYER}),
        ]
        _tag(lines, obj.id, why, str(obj.metadata.get("species") or obj.type))
        created.extend(lines)
        summary.count(USER_REMOVED_LAYER)

    for obj in scene.objects:
        if obj.type in _STRUCTURAL:
            continue
        base = base_objects.get(obj.id)
        if base is not None and not _changed(base, obj):
            continue  # как в исходнике -- уже есть в своём слое
        if base is not None:
            removed_mark(base, "исходный объект перенесён или изменён пользователем")
            layer = result_layer(obj.model_copy(update={"metadata": {**obj.metadata, "generated": False}}))
        else:
            layer = result_layer(obj)
        entities = _write_point_object(doc, msp, obj, catalog, layer=layer)
        _tag(entities, obj.id, *_plant_notes(obj))
        created.extend(entities)
        summary.count(layer)

    for obj in baseline.objects:
        if obj.type not in _STRUCTURAL and obj.id not in current_ids:
            removed_mark(obj, "исходный объект удалён пользователем")

    base_zone_ids = {z.id for z in baseline.restrictions}
    for zone in scene.restrictions:
        if zone.id in base_zone_ids or zone.type == "selection" or len(zone.polygon) < 3:
            continue
        layer = _safe_layer_name(zone.name, "NEW_PATHS") if is_greenplan_zone(zone) else USER_ZONES_LAYER
        _ensure_layer(doc, layer, _ACI_BY_SEVERITY.get(zone.severity, 7))
        pl = msp.add_lwpolyline([(p.x, p.z) for p in zone.polygon], close=True, dxfattribs={"layer": layer})
        _tag([pl], zone.id, zone.name, zone.message)
        created.append(pl)
        summary.count(layer)

    new_lawns = [a for a in scene.lawns if a.status == "new"]
    if new_lawns:
        _ensure_layer(doc, LAWN_LAYER, _ACI_LAWN)
        for lawn in new_lawns:
            hatch = msp.add_hatch(color=_ACI_LAWN, dxfattribs={"layer": LAWN_LAYER})
            hatch.paths.add_polyline_path([(p.x, p.z) for p in lawn.polygon], is_closed=True, flags=const.BOUNDARY_PATH_EXTERNAL)
            for hole in lawn.holes:
                hatch.paths.add_polyline_path([(p.x, p.z) for p in hole], is_closed=True, flags=const.BOUNDARY_PATH_OUTERMOST)
            _tag([hatch], lawn.id, f"газон {lawn.area_sqm:.0f} м²")
            created.append(hatch)
            summary.count(LAWN_LAYER)

    for entity in created:
        entity.transform(to_source)
    return summary
