"""
Операции правки существующего и сложные операции правки текстом (часть
PlanApplier, text_editor/applier.py): дорожка в обход препятствий (connect),
удаление/замена/прореживание по условию, масштаб, разворот, выравнивание
ряда, именованные зоны (define_zone) и дизайн двора целиком (design_area,
courtyard_design.py).
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Optional

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from core.placement import (
    MAX_AREA_RADIUS_M,
    MAX_SPACING_M,
    MIN_SPACING_M,
    TARGET_LABELS,
    PointIndex,
    clamp,
)
from core.placement_geometry import shortest_path
from core.schemas import Point2, Point3, RestrictionZone, SceneObject
from text_editor.courtyard_design import DEFAULT_ELEMENTS, DESIGN_ITEM_IDS, ELEMENTS, CourtyardDesigner
from text_editor.courtyard_layout import STYLES
from text_editor.operations import (
    AlignAlongOp,
    ConnectOp,
    DefineZoneOp,
    DesignAreaOp,
    FaceOp,
    RemoveWhereOp,
    ReplaceWhereOp,
    ResizeOp,
    ThinOutOp,
)
from text_editor.plan_common import (
    _OBJECT_FORMS,
    ALIGN_MATCH_REACH_M,
    DEFAULT_REMOVE_DISTANCE_M,
    DEFAULT_REMOVE_RADIUS_M,
    MAX_SCALE,
    MIN_SCALE,
    _default_spacing,
    _half_depth,
    _is_oriented,
    _labels,
    _plural,
)
from text_editor.prompt import _pack_substitutes

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")


class EditingOpsMixin:
    """См. докстринг модуля. Методы работают поверх атрибутов PlanApplier
    (self.placer, self.objects, self.applied/rejected/warnings, _create/_plant/
    _spot/_pool/_editable) -- сам по себе миксин не используется."""

    def _routable_region(self):
        """Место, где вообще можно провести НОВУЮ дорожку для connect:
        участок минус здания и явно непроходимые для пешехода зоны (дорога,
        площадка, ограждённая подстанция). Коммуникации (трубы, кабели)
        пересекать можно -- то же решение, что в courtyard_design.py (см.
        его докстринг): лёгкое мощение не мешает их обслуживанию, в отличие
        от капитальной постройки. Парковку намеренно не исключаем: чаще
        всего к НЕЙ САМОЙ и нужно подвести дорожку, а не обходить её."""
        if self.placer.site is None:
            return None
        blockers = [
            geom
            for zone, geom in self.placer.zones
            if zone.type in ("building", "road", "playground_zone", "transformer")
        ]
        area = self.placer.site.buffer(-0.6)
        return area.difference(unary_union(blockers)) if blockers else area

    def _resolve_endpoint(
        self, what: str, obj_id: Optional[str], target: Optional[str], x: Optional[float], z: Optional[float], label: str
    ):
        """Точка или зона-цель для одного конца операции (connect, line_of,
        enclose, face, duplicate_near). Ровно один способ задания должен
        сработать: по id объекта, по имени цели или явными координатами."""
        if obj_id is not None:
            obj = self.objects.get(obj_id)
            if obj is None:
                self.rejected.append(f"{what}: {label} — объекта {obj_id!r} нет")
                return None
            return Point(obj.position.x, obj.position.z)
        if target is not None:
            geom = self.placer.target_geometry(target)
            if geom is None:
                self.rejected.append(f"{what}: {label} — на участке нет цели «{TARGET_LABELS.get(target, target)}»")
                return None
            return geom
        if x is not None and z is not None:
            return Point(x, z)
        self.rejected.append(f"{what}: не указана точка «{label}» (id, target или x/z)")
        return None

    def _nearby_obstacles(self, a: Point, b: Point) -> list[Polygon]:
        """Здания и явно непроходимые зоны, которые вообще могут помешать
        связи a-b -- дом на другом конце участка в граф видимости включать
        незачем, только те, что рядом с этим конкретным отрезком."""
        line = LineString([(a.x, a.y), (b.x, b.y)])
        bbox = line.buffer(max(15.0, a.distance(b) * 0.5))
        obstacles = [
            geom
            for zone, geom in self.placer.zones
            if zone.type in ("building", "road", "playground_zone", "transformer") and geom.intersects(bbox)
        ]
        obstacles.sort(key=lambda g: g.distance(line))
        return obstacles

    def connect(self, op: ConnectOp) -> None:
        """Дорожка между двумя точками/зонами: design_area строит целую сеть
        и уже сама подводит её к подъездам и к парковке, но не умеет вести
        дорожку к произвольному объекту (например к фонтану, добавленному
        отдельно) или дотягивать существующую сеть до цели по отдельной
        просьбе -- для этого и нужна эта операция. Прямая, перекрытая
        зданием, не отклоняется сразу: сначала планировщик пробует обойти
        препятствие по графу видимости (placement_geometry.shortest_path) -- прямая
        линия это лишь частный случай кратчайшего пути."""
        a = self._resolve_endpoint("connect", op.from_id, op.from_target, op.from_x, op.from_z, "начало")
        b = self._resolve_endpoint("connect", op.to_id, op.to_target, op.to_x, op.to_z, "конец")
        if a is None or b is None:
            return
        item = self.by_id.get("path_segment")
        if item is None:
            self.rejected.append("connect: в каталоге нет мощения (path_segment)")
            return
        area = self._routable_region()
        if area is None:
            self.rejected.append("connect: не определена граница участка")
            return

        near_a, near_b = nearest_points(a, b)
        direct = near_a.distance(near_b)
        if direct < 1.0:
            self.rejected.append("connect: точки и так рядом — соединять нечего")
            return

        route = shortest_path(near_a, near_b, self._nearby_obstacles(near_a, near_b))
        if route is None:
            self.rejected.append("connect: препятствие полностью перекрывает связь — даже в обход пути нет")
            return

        clipped = route.intersection(area)
        pieces = [clipped] if clipped.geom_type == "LineString" else [g for g in getattr(clipped, "geoms", []) if g.geom_type == "LineString"]
        covered = sum(p.length for p in pieces)
        if covered < route.length - 1.0:
            self.rejected.append("connect: даже в обход препятствий провести дорожку не удалось")
            return

        width = item.dimensions.width or 2.0
        placed = 0
        for piece in pieces:
            length = piece.length
            if length < 0.5:
                continue
            count = max(1, round(length / width))
            for i in range(count):
                d = (i + 0.5) * length / count
                p = piece.interpolate(d)
                ahead = piece.interpolate(min(d + 0.5, length))
                tx, tz = ahead.x - p.x, ahead.y - p.y
                rotation = math.degrees(math.atan2(-tz, tx)) if (tx or tz) else 0.0
                self._create(item, p.x, p.y, rotation)
                placed += 1
        if placed == 0:
            self.rejected.append("connect: не нашлось места для мощения")
            return
        detour = ", в обход препятствия" if route.length > direct + 0.5 else ""
        self.applied.append(f"проложена дорожка: {_plural(placed, _OBJECT_FORMS)}, {covered:.0f} м{detour}")

    def _matching(
        self,
        object_types: list[str],
        target: Optional[str],
        distance_m: Optional[float],
        x: Optional[float],
        z: Optional[float],
        radius_m: Optional[float],
        what: str,
    ) -> Optional[tuple[list[SceneObject], str]]:
        """Существующие объекты этих типов, попадающие под необязательные
        фильтры (у цели ближе distance_m и/или в радиусе от точки) --
        (список, подпись условий отбора для сообщений) или None при жёсткой
        ошибке (тип менять нельзя, цели нет на участке). Общий отбор для
        remove_where, replace_where, thin_out, resize, face, align_along,
        set_count -- каждая применяет свою правку к одному и тому же набору.
        Пустой object_types -- все редактируемые типы сразу ("очисти эту
        зону" не должно требовать перечислять всё, что там может стоять)."""
        if not object_types:
            types = sorted(self.editable)
        else:
            types = list(dict.fromkeys(object_types))
            locked = [t for t in types if t not in self.editable]
            types = [t for t in types if t in self.editable]
            if locked:
                bucket = self.warnings if types else self.rejected
                bucket.append(f"{what}: объекты «{', '.join(locked)}» менять нельзя")
            if not types:
                return None

        conditions = []
        distance = None
        if target is not None:
            if self.placer.target_geometry(target) is None:
                self.rejected.append(f"{what}: на участке нет цели «{TARGET_LABELS.get(target, target)}»")
                return None
            distance = clamp(distance_m if distance_m is not None else DEFAULT_REMOVE_DISTANCE_M, 0.0, MAX_AREA_RADIUS_M)
            conditions.append(f"ближе {distance:.1f} м: {TARGET_LABELS.get(target, target)}")
        circle = None
        if x is not None and z is not None:
            circle = (x, z, clamp(radius_m or DEFAULT_REMOVE_RADIUS_M, 0.5, MAX_AREA_RADIUS_M))
            conditions.append(f"в радиусе {circle[2]:.0f} м от ({x:.1f}, {z:.1f})")

        matches = []
        for obj in self.objects.values():
            if obj.type not in types:
                continue
            ox, oz = obj.position.x, obj.position.z
            if circle is not None and math.hypot(ox - circle[0], oz - circle[1]) > circle[2]:
                continue
            if target is not None and self.placer.target_distance(target, ox, oz) > distance:
                continue
            matches.append(obj)
        scope = f" ({'; '.join(conditions)})" if conditions else ""
        return matches, scope

    def remove_where(self, op: RemoveWhereOp) -> None:
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, "удаление")
        if found is None:
            return
        matches, scope = found
        types_label = ", ".join(op.object_types) if op.object_types else "все типы"
        for obj in matches:
            del self.objects[obj.id]
            self.placer.release(obj.id)
        if not matches:
            self.rejected.append(f"удаление {types_label}{scope}: подходящих объектов нет")
            return
        self.applied.append(f"удалено {_plural(len(matches), _OBJECT_FORMS)} ({types_label}){scope}")

    def replace_where(self, op: ReplaceWhereOp) -> None:
        """Новый вид проходит ту же проверку, что и при add: другой габарит
        или отступ может здесь не поместиться, тогда объект остаётся как
        был, а не пропадает и не встаёт с нарушением."""
        what = "замена вида"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, what)
        if found is None:
            return
        matches, scope = found
        if not matches:
            self.rejected.append(f"{what}{scope}: подходящих объектов нет")
            return

        replaced = 0
        skipped = 0
        for i, obj in enumerate(matches):
            new_item = items[i % len(items)]
            x, z = obj.position.x, obj.position.z
            self.placer.release(obj.id)
            if not self.placer.is_free(x, z, new_item.setback_kind, obj_type=new_item.object_type, species=new_item.label):
                self.placer.occupy(obj.id, x, z, obj.type)
                skipped += 1
                continue
            del self.objects[obj.id]
            self._create(new_item, x, z, math.degrees(obj.rotation))
            replaced += 1
        if replaced == 0:
            self.rejected.append(f"{what}{scope}: новый вид здесь не помещается (другой габарит или отступ)")
            return
        self.applied.append(f"{what}{scope}: {_plural(replaced, _OBJECT_FORMS)} → {_labels(items)}")
        if skipped:
            self.warnings.append(f"{what}: {skipped} объектов оставлено как есть — новый вид там не помещается")

    def thin_out(self, op: ThinOutOp) -> None:
        what = "прореживание"
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, what)
        if found is None:
            return
        matches, scope = found
        if not matches:
            self.rejected.append(f"{what}{scope}: подходящих объектов нет")
            return

        spacing = clamp(op.min_spacing_m or 2.0, MIN_SPACING_M, MAX_SPACING_M)
        kept = PointIndex(spacing)
        removed = 0
        for obj in matches:
            x, z = obj.position.x, obj.position.z
            if kept.has_within(x, z, spacing):
                del self.objects[obj.id]
                self.placer.release(obj.id)
                removed += 1
            else:
                kept.add(obj.id, x, z)
        if removed == 0:
            self.rejected.append(f"{what}{scope}: и так реже {spacing:.1f} м — убирать нечего")
            return
        self.applied.append(f"{what}{scope}: убрано {_plural(removed, _OBJECT_FORMS)}, оставлено не реже {spacing:.1f} м")

    def resize(self, op: ResizeOp) -> None:
        what = "масштаб"
        scale = clamp(op.scale, MIN_SCALE, MAX_SCALE)
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, what)
        if found is None:
            return
        matches, scope = found
        if not matches:
            self.rejected.append(f"{what}{scope}: подходящих объектов нет")
            return
        for obj in matches:
            self.objects[obj.id] = obj.model_copy(update={"scale": scale})
        self.applied.append(f"{what}{scope}: {_plural(len(matches), _OBJECT_FORMS)} → ×{scale:.2f}")
        if abs(scale - op.scale) > 1e-6:
            self.warnings.append(f"{what}: запрошено ×{op.scale:.2f}, вне допустимого диапазона — взято ×{scale:.2f}")

    def face(self, op: FaceOp) -> None:
        what = "разворот"
        at = self._resolve_endpoint(what, op.at_id, op.at_target, op.at_x, op.at_z, "цель")
        if at is None:
            return
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, what)
        if found is None:
            return
        matches, scope = found
        if not matches:
            self.rejected.append(f"{what}{scope}: подходящих объектов нет")
            return
        turned = 0
        for obj in matches:
            # nearest_points(at, точка) -- для одиночной точки at это она
            # сама, для зоны (at_target) -- ближайшая точка её контура.
            near, _ = nearest_points(at, Point(obj.position.x, obj.position.z))
            dx, dz = near.x - obj.position.x, near.y - obj.position.z
            if math.hypot(dx, dz) < 1e-6:
                continue
            rotation = math.degrees(math.atan2(-dz, dx))
            self.objects[obj.id] = obj.model_copy(update={"rotation": math.radians(rotation)})
            turned += 1
        if turned == 0:
            self.rejected.append(f"{what}{scope}: цель совпадает с объектами — разворачивать некуда")
            return
        self.applied.append(f"{what}{scope}: {_plural(turned, _OBJECT_FORMS)}")

    def align_along(self, op: AlignAlongOp) -> None:
        what = f"выравнивание вдоль: {TARGET_LABELS.get(op.target, op.target)}"
        if self.placer.target_geometry(op.target) is None:
            self.rejected.append(f"{what}: на участке таких объектов нет")
            return
        found = self._matching(op.object_types, op.target, ALIGN_MATCH_REACH_M, op.x, op.z, op.radius_m, what)
        if found is None:
            return
        matches, _ = found
        if not matches:
            self.rejected.append(f"{what}: подходящих объектов рядом нет")
            return

        sample = self.by_id.get(matches[0].metadata.get("catalogId"))
        kind = sample.setback_kind if sample else None
        spacing = clamp(op.spacing_m or (_default_spacing(sample) if sample else 3.0), MIN_SPACING_M, MAX_SPACING_M)
        offset = self.placer.min_offset(op.target, kind, _half_depth(sample) if sample else 0.5, sample.label if sample else None)
        if op.offset_m is not None:
            offset = max(offset, op.offset_m)
        oriented = _is_oriented(sample) if sample else False

        for obj in matches:
            self.placer.release(obj.id)
        points = self.placer.points_along(op.target, spacing, offset)
        used = PointIndex(spacing)
        moved = 0
        for obj in sorted(matches, key=lambda o: (o.position.x, o.position.z)):
            best = None
            for x, z, rotation in sorted(points, key=lambda p: math.hypot(p[0] - obj.position.x, p[1] - obj.position.z)):
                if used.has_within(x, z, 0.9 * spacing):
                    continue
                if self.placer.is_free(x, z, self._setback_kind(obj), obj_type=obj.type, species=self._species(obj)):
                    best = (x, z, rotation)
                    break
            if best is None:
                self.placer.occupy(obj.id, obj.position.x, obj.position.z, obj.type)
                continue
            x, z, rotation = best
            used.add(obj.id, x, z)
            update = {"position": Point3(x=x, y=obj.position.y, z=z)}
            if oriented:
                update["rotation"] = math.radians(rotation)
            self.objects[obj.id] = obj.model_copy(update=update)
            self.placer.occupy(obj.id, x, z, obj.type)
            moved += 1
        if moved == 0:
            self.rejected.append(f"{what}: не нашлось валидных мест в ряду")
            return
        self.applied.append(f"{what}: выровнено {_plural(moved, _OBJECT_FORMS)}, шаг {spacing:.1f} м")
        if moved < len(matches):
            self.warnings.append(f"{what}: {len(matches) - moved} объектов оставлено на месте — валидных мест в ряду не хватило")

    def define_zone(self, op: DefineZoneOp) -> None:
        """Выделить именованную зону: круг вокруг точки, объекта или цели.
        Зона добавляется и в placer (действует уже для следующих операций
        этого же плана), и в new_zones (попадает в возвращаемую сцену --
        иначе она осталась бы только внутренним состоянием планировщика и
        пропала бы после ответа)."""
        what = f"зона «{op.name}»"
        if not op.name.strip():
            self.rejected.append(f"{what}: не указано имя зоны")
            return

        center = None
        if op.around_id is not None or op.around_target is not None or (op.x is not None and op.z is not None):
            resolved = self._resolve_endpoint(what, op.around_id, op.around_target, op.x, op.z, "центр")
            if resolved is None:
                return
            center = resolved
        else:
            self.rejected.append(f"{what}: не указано место (x/z, around_id или around_target)")
            return

        radius = clamp(op.radius_m or 8.0, 1.0, MAX_AREA_RADIUS_M)
        geom = center.buffer(radius) if center.geom_type == "Point" else center.buffer(radius).union(center)
        if geom.is_empty:
            self.rejected.append(f"{what}: не удалось построить контур")
            return
        if geom.geom_type != "Polygon":
            geom = max(geom.geoms, key=lambda g: g.area)

        zone = RestrictionZone(
            id=f"zone_llm_{uuid.uuid4().hex[:8]}",
            type="custom",
            name=op.name,
            polygon=[Point2(x=x, z=z) for x, z in geom.exterior.coords[:-1]],
            severity=op.severity,
            minDistance=0.0,
            message=op.message or op.name,
        )
        self.new_zones.append(zone)
        self.placer.add_zone(zone)
        self.applied.append(f"{what}: выделена ({op.severity}), площадь {geom.area:.0f} м²")

    def design_area(self, op: DesignAreaOp) -> None:
        what = "дизайн двора"
        unknown = [e for e in op.elements if e not in ELEMENTS]
        if unknown:
            self.warnings.append(f"{what}: неизвестные элементы пропущены: {', '.join(unknown)}")
        elements = [e for e in op.elements if e in ELEMENTS] or list(DEFAULT_ELEMENTS)
        items = {catalog_id: self.by_id.get(catalog_id) for catalog_id in DESIGN_ITEM_IDS}
        missing = [catalog_id for catalog_id, item in items.items() if item is None]
        if missing:
            self.rejected.append(f"{what}: в каталоге нет {', '.join(missing)}")
            return

        # Деревья по умолчанию -- из пака (средние, обычные и колонновидные).
        substitutes = _pack_substitutes(self.catalog)
        default_trees = list({s.id: s for s in (substitutes.get(t) for t in ("tree_medium", "tree_pine")) if s}.values())
        trees = (self._pool(op.tree_ids, what) if op.tree_ids else None) or default_trees
        trees = trees or [item for item in self.catalog if item.category == "tree"][:3]
        bushes = (self._pool(op.bush_ids, what) if op.bush_ids else None) or [
            item for item in self.catalog if item.object_type == "bush"
        ]

        style = op.style if op.style in STYLES else "auto"
        if op.style and op.style not in STYLES:
            self.warnings.append(f"{what}: неизвестный шаблон {op.style!r}, подобран автоматически")

        radius = clamp(op.radius_m or 40.0, 5.0, MAX_AREA_RADIUS_M)
        designer = CourtyardDesigner(self.scene, self.placer, self._create)
        summary, notes = designer.run(elements, items, trees, bushes, op.x, op.z, radius, style)
        if summary is None:
            self.rejected.append(f"{what}: {'; '.join(notes)}")
            return
        self.applied.append(f"{what}: {summary}")
        self.warnings.extend(f"{what}: {note}" for note in notes)
