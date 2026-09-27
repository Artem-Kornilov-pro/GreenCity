"""
Групповые операции посадки правки текстом (часть PlanApplier, text_editor/applier.py):
ряды вдоль целей (place_along), группы и россыпь по области (place_in_area),
сплошной ковёр (cover_area), линия между точками (line_of), кольцо вокруг
(enclose), копия рядом (duplicate_near), доведение до N штук (set_count).
Координаты считает Placer, каждая точка проверяется по нормам.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

from core.placement import (
    FALLBACK_ROW_STEP_M,
    MAX_AREA_RADIUS_M,
    MAX_BULK_PLACEMENTS,
    MAX_SNAP_DISTANCE_M,
    TARGET_LABELS,
    PointIndex,
    clamp,
)
from core.placement_geometry import pick_near, pick_spread, spread_subset
from core.setback_norms import SpeciesArg
from text_editor.operations import (
    CoverAreaOp,
    DuplicateNearOp,
    EncloseOp,
    LineOfOp,
    PlaceAlongOp,
    PlaceInAreaOp,
    SetCountOp,
)
from text_editor.plan_common import (
    _OBJECT_FORMS,
    CANDIDATES_NEAR_POINT,
    CANDIDATES_PER_PLACEMENT,
    DEFAULT_ALONG_RADIUS_M,
    DEFAULT_NEAR_TARGET_M,
    _default_spacing,
    _half_depth,
    _is_oriented,
    _labels,
    _plural,
    _pool_kind,
    _pool_species,
    _spacing_for,
)

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")


class PlacementOpsMixin:
    """См. докстринг модуля. Методы работают поверх атрибутов PlanApplier
    (self.placer, self.objects, self.applied/rejected/warnings, _create/_plant/
    _spot/_pool/_editable) -- сам по себе миксин не используется."""

    # --- Групповые операции ------------------------------------------------

    def place_along(self, op: PlaceAlongOp) -> None:
        what = f"вдоль: {TARGET_LABELS.get(op.target, op.target)}"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        if op.max_count is not None and op.max_count < 1:
            self.rejected.append(f"{what}: max_count должен быть не меньше 1")
            return
        if self.placer.target_geometry(op.target) is None:
            self.rejected.append(f"{what}: на участке таких объектов нет")
            return

        kind = _pool_kind(items)
        species = _pool_species(items)
        spacing = _spacing_for(op.spacing_m, items)
        offset = self.placer.min_offset(op.target, kind, max(_half_depth(item) for item in items), species)
        if op.offset_m is not None:
            if op.offset_m < offset - 0.05:
                self.warnings.append(f"{what}: отступ {op.offset_m:.1f} м меньше нормы, взят {offset:.1f} м")
            offset = max(offset, op.offset_m)

        circle = None
        if op.x is not None and op.z is not None:
            circle = (op.x, op.z, clamp(op.radius_m or DEFAULT_ALONG_RADIUS_M, 1.0, MAX_AREA_RADIUS_M))

        # Вытянутые объекты (секция изгороди 2 м) проверяем и по концам: центр
        # может стоять по норме, а край -- заходить на дорожку.
        half_length = max((item.dimensions.width / 2 for item in items if _is_oriented(item)), default=0.0)

        placed = PointIndex(spacing)
        accepted: list[tuple[float, float, float]] = []
        slots = 0
        for row, row_offset in enumerate((offset, offset + FALLBACK_ROW_STEP_M)):
            for x, z, rotation in self.placer.points_along(op.target, spacing, row_offset):
                if circle is not None and math.hypot(x - circle[0], z - circle[1]) > circle[2]:
                    continue
                if row == 0:
                    slots += 1
                # Запасной ряд только заполняет пропуски основного: рядом с
                # уже принятой точкой его кандидат отсекается по шагу.
                if placed.has_within(x, z, 0.9 * spacing) or not self.placer.is_free(x, z, kind, species=species):
                    continue
                if half_length and not self._ends_fit(x, z, rotation, half_length, kind, species):
                    continue
                placed.add(str(len(accepted)), x, z)
                accepted.append((x, z, rotation))

        if not accepted:
            self.rejected.append(f"{what}: нет ни одного места без нарушений норм (проверено мест: {slots})")
            return

        limit = min(op.max_count or MAX_BULK_PLACEMENTS, MAX_BULK_PLACEMENTS)
        chosen = spread_subset(accepted, limit)
        used = self._plant(items, chosen)
        self.applied.append(
            f"{what}: {_plural(len(chosen), _OBJECT_FORMS)} ({used}), шаг {spacing:.1f} м, в {offset:.1f} м от края"
        )
        if op.max_count and len(chosen) < op.max_count:
            self.warnings.append(f"{what}: размещено {len(chosen)} из {op.max_count} — больше мест без нарушений норм нет")
        elif not op.max_count and len(accepted) > MAX_BULK_PLACEMENTS:
            self.warnings.append(f"{what}: за одну операцию не больше {MAX_BULK_PLACEMENTS}, остальные места пропущены")
        if not op.max_count and len(accepted) < 0.6 * slots:
            self.warnings.append(
                f"{what}: без нарушений норм подошло {len(accepted)} из {slots} мест ряда — остальные "
                "у сетей, зданий, парковок, фонарей или подъездов"
            )

    def _ends_fit(
        self, x: float, z: float, rotation_deg: float, half_length: float, kind: Optional[str], species: SpeciesArg = None
    ) -> bool:
        # Направление локальной оси X при повороте θ -- (cosθ, -sinθ), см.
        # Placer.points_along.
        angle = math.radians(rotation_deg)
        dx, dz = math.cos(angle) * half_length, -math.sin(angle) * half_length
        return self.placer.in_region(x + dx, z + dz, kind, species) and self.placer.in_region(x - dx, z - dz, kind, species)

    def _free_area(self, area_id: str):
        areas = self.placer.free_areas()
        label = area_id.strip().upper()
        if label.startswith("A") and label[1:].isdigit() and 1 <= int(label[1:]) <= len(areas):
            return areas[int(label[1:]) - 1]
        # Не "A1"/"A2" из free_areas -- возможно, имя зоны, выделенной
        # define_zone ("посади цветы в зоне «Клумба у входа»").
        return self.placer.target_geometry(area_id)

    def place_in_area(self, op: PlaceInAreaOp) -> None:
        what = "группа посадок"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        if op.count < 1:
            self.rejected.append(f"{what}: count должен быть не меньше 1")
            return
        count = min(op.count, MAX_BULK_PLACEMENTS)
        kind = _pool_kind(items)
        species = _pool_species(items)
        spacing = _spacing_for(op.spacing_m, items)

        area = None
        where = "по всему участку"
        if op.area is not None:
            area = self._free_area(op.area)
            if area is None:
                self.rejected.append(f"{what}: нет свободной области {op.area!r}")
                return
            where = f"по области {op.area}"
            # Проверяется точка-центр посадки, а не её крона/габарит. Для
            # free_areas это не страшно -- они сами уже вырезаны из допустимой
            # области с запасом (region()). А вот именованная зона (define_zone
            # или выделенная мышкой вручную) -- сырой полигон без единого
            # отступа, и дерево с центром у самого её края визуально вылезает
            # за границу, хотя формально "за границы" никто не просил. Отступаем
            # внутрь на радиус посадки; если зона меньше отступа целиком --
            # сажаем как есть, лучше по центру мелкой зоны, чем нигде.
            inset = area.buffer(-spacing / 2)
            if not inset.is_empty:
                area = inset

        if op.target is not None:
            # "Две скамейки у детской площадки": полоса вокруг цели, а не весь
            # участок (раньше поле target у группы молча отбрасывалось).
            geom = self.placer.target_geometry(op.target)
            if geom is None:
                self.rejected.append(f"{what}: на участке нет цели «{TARGET_LABELS.get(op.target, op.target)}»")
                return
            reach = clamp(op.distance_m or DEFAULT_NEAR_TARGET_M, 1.0, MAX_AREA_RADIUS_M)

            def near_band(reach: float):
                if op.target == "site_boundary":
                    return self.placer.site.difference(self.placer.site.buffer(-reach))
                return geom.buffer(reach).difference(geom)

            # Вплотную к площадке часто одни дорожки: если в полосе нет места
            # на всё, расширяем её (как и группу вокруг точки ниже).
            base_area = area
            for attempt in (reach, max(2 * reach, DEFAULT_NEAR_TARGET_M), reach + MAX_SNAP_DISTANCE_M):
                band = near_band(attempt)
                area = band if base_area is None else base_area.intersection(band)
                probe = self.placer.points_in_area(kind, spacing / 2, area, count * CANDIDATES_PER_PLACEMENT, species)
                if len([p for p in probe if self.placer.is_free(p[0], p[1], kind, species=species)]) >= count:
                    break
            if attempt > reach:
                self.warnings.append(f"{what}: ближе {reach:.0f} м к цели места нет — взято до {attempt:.0f} м")
            where = f"у цели «{TARGET_LABELS.get(op.target, op.target)}» (до {attempt:.0f} м)"

        if op.x is None or op.z is None:
            candidates = self.placer.points_in_area(kind, spacing / 2, area, count * CANDIDATES_PER_PLACEMENT, species)
            free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind, species=species)]
            chosen = pick_spread(free, count, 0.9 * spacing)
        else:
            center = (op.x, op.z)
            radius = clamp(op.radius_m or max(8.0, 1.2 * spacing * math.sqrt(count)), 1.0, MAX_AREA_RADIUS_M)
            # "У входа" в радиусе 5 м для дерева пусто по определению (5 м от
            # стены), поэтому при нехватке места круг один раз расширяем.
            for attempt in (radius, radius + MAX_SNAP_DISTANCE_M):
                circle = Point(center).buffer(attempt)
                within = circle if area is None else area.intersection(circle)
                candidates = self.placer.points_in_area(kind, spacing / 4, within, CANDIDATES_NEAR_POINT, species)
                free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind, species=species)]
                chosen = pick_near(free, count, 0.9 * spacing, center)
                if len(chosen) >= count:
                    break
            where = f"вокруг ({op.x:.1f}, {op.z:.1f}), радиус {attempt:.0f} м"

        if not chosen:
            self.rejected.append(f"{what} {where}: нет места без нарушений норм")
            return
        used = self._plant(items, chosen)
        self.applied.append(f"{what} {where}: {_plural(len(chosen), _OBJECT_FORMS)} ({used})")
        if len(chosen) < op.count:
            self.warnings.append(
                f"{what} {where}: размещено {len(chosen)} из {op.count} — больше мест без нарушений норм "
                f"при шаге {spacing:.1f} м нет"
            )

    def cover_area(self, op: CoverAreaOp) -> None:
        """Сплошной ковёр покрытия, а не редкая россыпь (см. докстринг
        CoverAreaOp): шаг сетки берётся почти равным габариту плитки, и
        забираются ВСЕ подошедшие места (до предела), а не спред-подвыборка."""
        what = "ковёр покрытия"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        kind = _pool_kind(items)
        width = max(item.dimensions.width or 2.0 for item in items)
        depth = max(item.dimensions.depth or item.dimensions.width or 2.0 for item in items)
        half_w, half_d = width / 2, depth / 2

        area = None
        where = "по всему участку"
        if op.area is not None:
            area = self._free_area(op.area)
            if area is None:
                self.rejected.append(f"{what}: нет свободной области {op.area!r}")
                return
            where = f"по области {op.area}"
        elif op.x is not None and op.z is not None:
            radius = clamp(op.radius_m or 15.0, 1.0, MAX_AREA_RADIUS_M)
            area = Point(op.x, op.z).buffer(radius)
            where = f"вокруг ({op.x:.1f}, {op.z:.1f}), радиус {radius:.0f} м"

        candidates = self.placer.points_in_area(kind, width * 0.95, area, MAX_BULK_PLACEMENTS * 3)
        # Проверяем весь прямоугольник плитки (4x4 м у газона), а не только
        # её центр -- см. region_contains: центр на 2 м от края уже
        # пропускал бы плитку, чей угол при этом торчит за границей участка
        # или залезает на здание/трубу.
        free = [
            p
            for p in candidates
            if self.placer.region_contains(
                Polygon(
                    [
                        (p[0] - half_w, p[1] - half_d),
                        (p[0] + half_w, p[1] - half_d),
                        (p[0] + half_w, p[1] + half_d),
                        (p[0] - half_w, p[1] + half_d),
                    ]
                ),
                kind,
            )
            and self.placer.blocker(p[0], p[1], kind) is None
        ]
        chosen = free[:MAX_BULK_PLACEMENTS]
        if not chosen:
            self.rejected.append(f"{what} {where}: нет места без нарушений норм")
            return
        for i, (x, z) in enumerate(chosen):
            self._create(items[i % len(items)], x, z, 0.0)
        self.applied.append(f"{what} {where}: {_plural(len(chosen), _OBJECT_FORMS)} ({_labels(items)})")
        if len(free) > len(chosen):
            self.warnings.append(
                f"{what}: уложено {len(chosen)} из {len(free)} возможных — за одну операцию не больше {MAX_BULK_PLACEMENTS}"
            )

    def line_of(self, op: LineOfOp) -> None:
        what = "линия"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        a = self._resolve_endpoint(what, op.from_id, op.from_target, op.from_x, op.from_z, "начало")
        b = self._resolve_endpoint(what, op.to_id, op.to_target, op.to_x, op.to_z, "конец")
        if a is None or b is None:
            return
        near_a, near_b = nearest_points(a, b)
        length = near_a.distance(near_b)
        if length < 1.0:
            self.rejected.append(f"{what}: точки и так рядом — соединять нечего")
            return

        kind = _pool_kind(items)
        species = _pool_species(items)
        spacing = _spacing_for(op.spacing_m, items)
        # Вытянутые объекты (секция изгороди) проверяем и по концам: центр
        # может стоять по норме, а край -- заходить на здание или в зону.
        half_length = max((item.dimensions.width / 2 for item in items if _is_oriented(item)), default=0.0)
        tx, tz = (near_b.x - near_a.x) / length, (near_b.y - near_a.y) / length
        rotation = math.degrees(math.atan2(-tz, tx))
        count = max(1, round(length / spacing))
        spots = []
        for i in range(count + 1):
            d = i * length / count
            x, z = near_a.x + tx * d, near_a.y + tz * d
            if self.placer.is_free(x, z, kind, species=species) and (
                not half_length or self._ends_fit(x, z, rotation, half_length, kind, species)
            ):
                spots.append((x, z, rotation))
        if not spots:
            self.rejected.append(f"{what}: нет места без нарушений норм по всей линии")
            return
        used = self._plant(items, spots)
        self.applied.append(f"{what}: {_plural(len(spots), _OBJECT_FORMS)} ({used}), {length:.0f} м")
        if len(spots) < count + 1:
            self.warnings.append(f"{what}: из {count + 1} точек по линии встало {len(spots)} — остальные нарушали бы нормы")

    def enclose(self, op: EncloseOp) -> None:
        what = "кольцо"
        items = self._pool(op.catalog_ids, what)
        if items is None:
            return
        kind = _pool_kind(items)
        species = _pool_species(items)
        half_depth = max(_half_depth(item) for item in items)

        if op.around_target is not None:
            geom = self.placer.target_geometry(op.around_target)
            if geom is None:
                self.rejected.append(f"{what}: на участке нет цели «{TARGET_LABELS.get(op.around_target, op.around_target)}»")
                return
            # Отступ не меньше нормативного: "огороди площадку с отступом 1 м"
            # при норме больше метра раньше отклонялось целиком, хотя
            # пользователю нужна изгородь, а не именно этот метр.
            least = self.placer.min_offset(op.around_target, kind, half_depth, species)
            offset = max(op.offset_m, least) if op.offset_m is not None else least
            if op.offset_m is not None and offset > op.offset_m + 1e-6:
                self.warnings.append(f"{what}: отступ {op.offset_m:.1f} м меньше нормы — взято {offset:.1f} м")
        else:
            center = self._resolve_endpoint(what, op.around_id, None, op.around_x, op.around_z, "центр")
            if center is None:
                return
            radius = clamp(op.radius_m or 6.0, 1.5, MAX_AREA_RADIUS_M)
            geom = center.buffer(radius)
            offset = op.offset_m if op.offset_m is not None else half_depth + 0.5

        spacing = _spacing_for(op.spacing_m, items)
        # Как и в line_of: у вытянутых объектов центр может стоять по норме,
        # а край -- нет.
        half_length = max((item.dimensions.width / 2 for item in items if _is_oriented(item)), default=0.0)

        def ring_spots(offset: float) -> tuple[list, int]:
            """(места кольца без нарушений, всего мест на кольце)."""
            band = geom.buffer(offset)
            polys = [band] if band.geom_type == "Polygon" else [g for g in getattr(band, "geoms", []) if g.geom_type == "Polygon"]
            rings = [r for poly in polys for r in (poly.exterior, *poly.interiors)]
            spots, total = [], 0
            for ring in rings:
                length = ring.length
                if length < 1.0:
                    continue
                count = max(1, int(length // spacing))
                total += count
                for i in range(count):
                    d = i * length / count
                    p = ring.interpolate(d)
                    ahead = ring.interpolate((d + 0.5) % length)
                    tx, tz = ahead.x - p.x, ahead.y - p.y
                    rotation = math.degrees(math.atan2(-tz, tx)) if (tx or tz) else 0.0
                    if self.placer.is_free(p.x, p.y, kind, species=species) and (
                        not half_length or self._ends_fit(p.x, p.y, rotation, half_length, kind, species)
                    ):
                        spots.append((p.x, p.y, rotation))
            return spots, total

        # Кольцо вплотную к цели часто перерезают дорожки и сети: если на нём
        # помещается меньше половины мест, пробуем чуть дальше и берём
        # лучшее -- огородить площадку на 2 м дальше лучше, чем не огородить.
        # (Эталон 23: площадку в 2 м обходят дорожки -- кольцо у края почти
        # целиком ложится на них, а за дорожками встаёт полностью.)
        accepted, total = ring_spots(offset)
        used_offset = offset
        for extra in (1.0, 2.5, 4.0, 6.0):
            if total and len(accepted) >= total / 2:
                break
            wider, wider_total = ring_spots(offset + extra)
            if len(wider) > len(accepted):
                accepted, total, used_offset = wider, wider_total, offset + extra
        if used_offset > offset:
            self.warnings.append(f"{what}: у самого края мешают дорожки или сети — кольцо отодвинуто на {used_offset:.1f} м")
        if not accepted:
            self.rejected.append(f"{what}: нет места без нарушений норм по контуру")
            return
        chosen = spread_subset(accepted, min(MAX_BULK_PLACEMENTS, len(accepted)))
        used = self._plant(items, chosen)
        self.applied.append(f"{what}: {_plural(len(chosen), _OBJECT_FORMS)} ({used}), шаг {spacing:.1f} м")

    def duplicate_near(self, op: DuplicateNearOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        item = self.by_id.get(obj.metadata.get("catalogId"))
        if item is None:
            self.rejected.append(f"duplicate_near {op.id}: объект не из каталога, скопировать нечем")
            return
        what = f"копия «{item.label}»"
        near = self._resolve_endpoint(what, op.near_id, op.near_target, op.near_x, op.near_z, "рядом с")
        if near is None:
            return

        count = int(clamp(op.count, 1, 20))
        kind = item.setback_kind
        spacing = _default_spacing(item)
        # Как и у группы вокруг точки: у подъезда рядом уже стоят прошлые
        # посадки, и на "ещё столько же" места в первом круге не хватало --
        # копий выходило меньше просьбы, причём молча.
        reach = max(8.0, spacing * count) if near.geom_type == "Point" else 10.0
        for attempt in (reach, reach + MAX_SNAP_DISTANCE_M):
            within = near.buffer(attempt)
            candidates = self.placer.points_in_area(kind, spacing / 4, within, CANDIDATES_NEAR_POINT, item.label)
            free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind, species=item.label)]
            if near.geom_type == "Point":
                chosen = pick_near(free, count, 0.9 * spacing, (near.x, near.y))
            else:
                chosen = pick_spread(free, count, 0.9 * spacing)
            if len(chosen) >= count:
                break
        if not chosen:
            self.rejected.append(f"{what}: рядом нет места без нарушений норм")
            return
        for x, z in chosen:
            self._create(item, x, z, math.degrees(obj.rotation))
        self.applied.append(f"{what}: добавлено {_plural(len(chosen), _OBJECT_FORMS)}")
        if len(chosen) < op.count:
            self.warnings.append(f"{what}: добавлено {len(chosen)} из {op.count} — больше мест без нарушений норм рядом нет")

    def set_count(self, op: SetCountOp) -> None:
        what = "нужное количество"
        count = int(clamp(op.count, 0, MAX_BULK_PLACEMENTS))
        found = self._matching(op.object_types, op.target, op.distance_m, op.x, op.z, op.radius_m, what, op.species, op.ids)
        if found is None:
            return
        matches, scope = found
        current = len(matches)
        if current == count:
            self.applied.append(f"{what}{scope}: уже {current} — без изменений")
            return
        if current > count:
            for obj in matches[count:]:
                del self.objects[obj.id]
                self.placer.release(obj.id)
            self.applied.append(f"{what}{scope}: было {current}, убрано {current - count}, осталось {count}")
            return

        need = count - current
        items = self._pool(op.catalog_ids, what) if op.catalog_ids else None
        if items is None and matches:
            existing = self.by_id.get(matches[0].metadata.get("catalogId"))
            items = [existing] if existing else None
        if items is None:
            self.rejected.append(f"{what}: не хватает {need}, но не указано, чем добавлять (catalog_ids)")
            return

        kind = _pool_kind(items)
        species = _pool_species(items)
        spacing = _spacing_for(None, items)
        if op.x is not None and op.z is not None:
            radius = clamp(op.radius_m or max(10.0, 1.2 * spacing * math.sqrt(need)), 1.0, MAX_AREA_RADIUS_M)
            within = Point(op.x, op.z).buffer(radius)
            candidates = self.placer.points_in_area(kind, spacing / 4, within, CANDIDATES_NEAR_POINT, species)
            free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind, species=species)]
            chosen = pick_near(free, need, 0.9 * spacing, (op.x, op.z))
        else:
            candidates = self.placer.points_in_area(kind, spacing / 2, None, need * CANDIDATES_PER_PLACEMENT, species)
            free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind, species=species)]
            chosen = pick_spread(free, need, 0.9 * spacing)
        if not chosen:
            self.rejected.append(f"{what}{scope}: было {current}, добавить не удалось — нет места")
            return
        used = self._plant(items, chosen)
        self.applied.append(f"{what}{scope}: было {current}, добавлено {_plural(len(chosen), _OBJECT_FORMS)} ({used}), стало {current + len(chosen)}")
        if len(chosen) < need:
            self.warnings.append(f"{what}: нужно было ещё {need - len(chosen)} — больше мест без нарушений норм нет")
