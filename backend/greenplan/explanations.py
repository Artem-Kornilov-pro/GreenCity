"""
Объяснение КАЖДОЙ посадки со ссылкой на нормативный акт и пункт -- ТЗ,
"Что должен уметь сервис", п. 8 (обязательное требование к качеству), и
7.2, п. 6: машиночитаемый файл (JSON / CSV) вместе с планом.

Для каждой новой посадки:
* что и где: вид, координаты (в метрах сцены и в координатах исходного
  чертежа), слой результата DXF (NEW_* -- GreenPlan, USER_* -- правка
  пользователя) -- id тот же, что в XDATA сущности DXF (exchange/dxf_overlay.py);
* почему здесь именно этот тип: место на участке (вид зоны), приём
  озеленения, проект-аналог и стиль участка (pattern_assignment.py);
* почему этот вид: ассортимент для озеленения Москвы по типу территории,
  базовый ассортимент ППМ 515-ПП, без инвазивных ППМ 369-ПП
  (species_selection.py);
* почему можно: ближайшие ограничения -- сети, здания, дороги, дорожки,
  соседние объекты -- с фактическим расстоянием, требуемым отступом и
  пунктом НПА, который его задаёт (core/setback_norms.setback_basis).

И отдельно -- ОТКЛОНЁННЫЕ кандидаты (ТЗ: "если посадка запрещена или
отклонена алгоритмом вблизи коммуникаций / охранных зон -- также фиксируется
причина со ссылкой на норму"): точка, которую расстановка рассматривала, но
не поставила, с тем же разбором причины. Их собирает RejectionLog во время
расстановки (greenplan/deterministic_placement.py).
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from contextvars import ContextVar
from typing import Optional

from pydantic import BaseModel
from shapely.geometry import Point
from shapely.strtree import STRtree

from core.placement import OBJECT_CLEARANCE_M, POINT_CLEARANCE_M, SITE_CLEARANCE_M, Placer
from core.schemas import Scene, SceneObject
from core.setback_norms import MAX_SETBACK_M, setback_basis
from exchange.export_dxf import is_source_object, result_layer
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pattern_library import PATTERN_LIBRARY, STYLE_LABELS

# Ограничения не дальше (требуемый отступ + запас) -- те, что реально
# определили место посадки; дальние к объяснению ничего не добавляют.
NEAR_MARGIN_M = 5.0
MAX_CONSTRAINTS_PER_PLANT = 5
# Отклонённых кандидатов -- подробно не больше стольких на участок (остальные
# только считаются): разбор причины -- обход ближайших зон, и на крупном
# участке кандидатов десятки тысяч.
MAX_DETAILED_REJECTIONS = 300

ASSORTMENT_SOURCE = "ассортимент для озеленения Москвы; ППМ 515-ПП (базовый ассортимент); ППМ 369-ПП (без инвазивных видов)"
LAMP_SOURCE = "СП 42.13330.2016, табл. 9.1; ППМ 743-ПП, табл. 3.6.1 (опора освещения — дерево)"
PLANNER_RULE = "правило планировщика (не норма): зазор между объектами"

ZONE_TYPE_LABELS = {
    "building": "здание",
    "road": "проезжая часть",
    "pedestrian_path": "пешеходная дорожка / тротуар",
    "water_pipeline": "водопровод",
    "sewer": "канализация",
    "gas_pipeline": "газопровод",
    "heat_network": "теплосеть",
    "electrical": "силовой кабель",
    "signal_cable": "кабель связи",
    "overhead_power_line": "воздушная ЛЭП",
    "playground_zone": "детская площадка",
    "parking": "парковка",
    "protected_zone": "охранная зона",
}


class Constraint(BaseModel):
    what: str  # "здание", "водопровод", "дерево tree_012", ...
    zone_id: Optional[str] = None
    distance_m: float
    required_m: float
    ok: bool
    norm: str


class PlantExplanation(BaseModel):
    id: str
    type: str
    species: str
    status: str  # "посажено"
    origin: str  # GreenPlan / правка пользователя
    dxf_layer: str
    x: float
    z: float
    dxf_x: Optional[float] = None
    dxf_y: Optional[float] = None
    zone_kind: Optional[str] = None
    pattern: Optional[str] = None
    source_project: Optional[str] = None
    site_style: Optional[str] = None
    reason: str
    species_basis: str
    constraints: list[Constraint]


class Rejection(BaseModel):
    x: float
    z: float
    type: str
    species: str
    zone_kind: Optional[str] = None
    pattern: Optional[str] = None
    reason: str
    norm: str
    distance_m: Optional[float] = None
    required_m: Optional[float] = None


class RejectionStats(BaseModel):
    # Отклонённые по НОРМЕ (сеть, здание, порода у здания/теплосети) --
    # подробно, с пунктом НПА. Отказы по правилам самого планировщика
    # (зазор между соседними объектами) -- только счётчиком: их тысячи, и
    # к нормам они отношения не имеют.
    detailed: list[Rejection] = []
    total: int = 0
    by_rule: dict[str, int] = {}


class RestrictedZone(BaseModel):
    """Зона запрета посадки: ограничение и нормативная полоса вокруг него,
    в которой расстановка точки не рассматривает вовсе."""

    zone_id: str
    what: str
    tree_setback_m: float
    tree_norm: str
    bush_setback_m: float
    bush_norm: str


class _ZoneIndex:
    """Зоны ограничений участка в STRtree -- чтобы разбор причин не обходил
    тысячи зон реального участка на каждую точку."""

    def __init__(self, placer: Placer):
        self.placer = placer
        self.items = [(zone, geom) for zone, geom in placer.zones if zone.severity != "allowed"]
        self.tree = STRtree([geom for _, geom in self.items]) if self.items else None

    def near(self, x: float, z: float, reach: float):
        if self.tree is None:
            return []
        point = Point(x, z)
        hits = self.tree.query(point.buffer(reach))
        return [(self.items[i][0], self.items[i][1].distance(point)) for i in hits]


def _zone_constraints(index: _ZoneIndex, x: float, z: float, kind: str, species: str) -> list[Constraint]:
    out = []
    for zone, distance in index.near(x, z, MAX_SETBACK_M + NEAR_MARGIN_M):
        required, norm = setback_basis(zone.type, kind, zone.minDistance, species)
        if distance > required + NEAR_MARGIN_M:
            continue
        label = ZONE_TYPE_LABELS.get(zone.type, zone.name or zone.type)
        out.append(Constraint(what=label, zone_id=zone.id, distance_m=round(distance, 2), required_m=required, ok=distance >= required, norm=norm))
    return out


def _object_constraints(placer: Placer, obj_id: Optional[str], x: float, z: float, kind: str) -> list[Constraint]:
    out = []
    reach = max(OBJECT_CLEARANCE_M, *(v for norms in POINT_CLEARANCE_M.values() for v in norms.values())) + NEAR_MARGIN_M
    for key, other_type, distance in placer._objects.within(x, z, reach):
        if key == obj_id:
            continue
        norm_value = POINT_CLEARANCE_M.get(other_type, {}).get(kind)
        if norm_value is None:
            continue
        norm = LAMP_SOURCE if other_type == "lamp" else PLANNER_RULE
        out.append(Constraint(what=f"{other_type} {key}", distance_m=round(distance, 2), required_m=norm_value, ok=distance >= norm_value, norm=norm))
    return out


def _to_dxf(scene: Scene, x: float, z: float) -> tuple[Optional[float], Optional[float]]:
    """Координаты в системе исходного чертежа (обратное к parser Transform)."""
    scale = scene.meta.scale or 1.0
    origin = scene.meta.origin or {}
    if "x" not in origin:
        return None, None
    return round(x / scale + float(origin["x"]), 3), round(z / scale + float(origin["y"]), 3)


def _plant_kind(obj: SceneObject) -> Optional[str]:
    return {"tree": "tree", "bush": "bush", "hedge_segment": "bush"}.get(obj.type)


def explain_plants(scene: Scene, assignments: list[ZoneAssignment]) -> list[PlantExplanation]:
    """Объяснение каждой посадки (дерево, кустарник, изгородь), которой нет в
    исходном чертеже: GreenPlan и правки пользователя."""
    by_zone = {a.zone_id: a for a in assignments}
    placer = Placer(scene)
    for obj in scene.objects:
        placer.occupy(obj.id, obj.position.x, obj.position.z, obj.type)
    index = _ZoneIndex(placer)
    site_style = next((a.site_style for a in assignments if a.site_style), None)

    out = []
    for obj in scene.objects:
        kind = _plant_kind(obj)
        if kind is None or is_source_object(obj):
            continue
        species = str(obj.metadata.get("species") or obj.metadata.get("label") or obj.type)
        x, z = obj.position.x, obj.position.z
        constraints = _zone_constraints(index, x, z, kind, species) + _object_constraints(placer, obj.id, x, z, kind)
        # Нормативные ограничения -- первыми: объяснение "почему здесь можно"
        # опирается на них, а не на зазор до соседнего дерева.
        constraints.sort(key=lambda c: (c.norm == PLANNER_RULE, c.distance_m - c.required_m))
        assignment = by_zone.get(obj.metadata.get("zone_id"))
        generated = bool(obj.metadata.get("generated"))
        if generated and assignment is not None:
            pattern = PATTERN_LIBRARY[assignment.pattern_id].label if assignment.pattern_id in PATTERN_LIBRARY else assignment.pattern_id
            analog = f"по аналогии с проектом «{assignment.source_project}»" if assignment.source_project else "типовое решение в стиле участка"
            reason = f"{pattern} — {analog}"
            basis = f"{assignment.species_basis or ASSORTMENT_SOURCE}"
        else:
            pattern = None
            reason = "добавлено пользователем (ИИ-ассистент или вручную); место проверено по тем же нормам"
            basis = ASSORTMENT_SOURCE
        normative = [c for c in constraints if c.norm != PLANNER_RULE]
        if normative:
            nearest = normative[0]
            reason += f"; ближайшее ограничение — {nearest.what}: {nearest.distance_m:.1f} м при норме {nearest.required_m:.1f} м ({nearest.norm})"
        else:
            reason += f"; нормируемых ограничений ближе {NEAR_MARGIN_M:.0f} м сверх нормы нет"
        dxf_x, dxf_y = _to_dxf(scene, x, z)
        out.append(
            PlantExplanation(
                id=obj.id,
                type=obj.type,
                species=species,
                status="посажено",
                origin="GreenPlan" if generated else "правка пользователя",
                dxf_layer=result_layer(obj),
                x=round(x, 3),
                z=round(z, 3),
                dxf_x=dxf_x,
                dxf_y=dxf_y,
                zone_kind=assignment.zone_kind if assignment else None,
                pattern=pattern,
                source_project=assignment.source_project if assignment and generated else None,
                site_style=STYLE_LABELS.get(site_style) if site_style and generated else None,
                reason=reason,
                species_basis=basis,
                constraints=constraints[:MAX_CONSTRAINTS_PER_PLANT],
            )
        )
    return out


# --- Отклонённые кандидаты -----------------------------------------------------


class RejectionLog:
    """Копит отклонённые при расстановке точки: первые MAX_DETAILED_REJECTIONS
    -- с разбором причины и нормы, остальные только считает."""

    def __init__(self):
        self.detailed: list[Rejection] = []
        self.total = 0
        self.by_rule: Counter = Counter()
        self._indexes: dict[int, _ZoneIndex] = {}

    def record(self, placer: Placer, x: float, z: float, obj_type: str, species: str, zone_kind: Optional[str], pattern: Optional[str]):
        self.total += 1
        if len(self.detailed) >= MAX_DETAILED_REJECTIONS:
            self.by_rule["не разобрано (сверх лимита подробного списка)"] += 1
            return
        kind = {"tree": "tree", "bush": "bush", "hedge_segment": "bush"}.get(obj_type)
        index = self._indexes.get(id(placer))
        if index is None:
            index = self._indexes[id(placer)] = _ZoneIndex(placer)
        rejection = _rejection(placer, index, x, z, obj_type, kind, species, zone_kind, pattern)
        self.by_rule[rejection.norm] += 1
        if rejection.norm != PLANNER_RULE:
            self.detailed.append(rejection)

    def stats(self) -> RejectionStats:
        return RejectionStats(detailed=self.detailed, total=self.total, by_rule=dict(self.by_rule))


def _rejection(placer, index, x, z, obj_type, kind, species, zone_kind, pattern) -> Rejection:
    base = dict(x=round(x, 3), z=round(z, 3), type=obj_type, species=species, zone_kind=zone_kind, pattern=pattern)
    point = Point(x, z)
    if placer.site is not None and (not placer.site.contains(point) or placer.site.exterior.distance(point) < SITE_CLEARANCE_M):
        return Rejection(**base, reason="у самой границы участка", norm=PLANNER_RULE)
    if kind is not None:
        violated = [c for c in _zone_constraints(index, x, z, kind, species) if not c.ok]
        if violated:
            worst = min(violated, key=lambda c: c.distance_m - c.required_m)
            return Rejection(
                **base, reason=f"ближе нормы к объекту «{worst.what}»", norm=worst.norm,
                distance_m=worst.distance_m, required_m=worst.required_m,
            )
    hit = placer.blocker(x, z, kind, OBJECT_CLEARANCE_M, obj_type)
    if hit is not None:
        key, required, distance = hit
        norm = LAMP_SOURCE if key.startswith("lamp") and kind == "tree" else PLANNER_RULE
        return Rejection(**base, reason=f"рядом объект {key}", norm=norm, distance_m=round(distance, 2), required_m=required)
    return Rejection(**base, reason="вне допустимой области посадки", norm=PLANNER_RULE)


_active_log: ContextVar[Optional[RejectionLog]] = ContextVar("greenplan_rejections", default=None)


def start_rejection_log() -> tuple[RejectionLog, object]:
    log = RejectionLog()
    return log, _active_log.set(log)


def stop_rejection_log(token) -> None:
    _active_log.reset(token)


def record_rejection(placer: Placer, x: float, z: float, obj_type: str, species: str, zone_kind=None, pattern=None) -> None:
    """Вызывается расстановкой, когда точка не прошла проверку норм. Без
    активного журнала (тесты, правка текстом) -- ничего не делает."""
    log = _active_log.get()
    if log is not None:
        log.record(placer, x, z, obj_type, species, zone_kind, pattern)


# --- Выгрузка ----------------------------------------------------------------------


def restricted_zones(scene: Scene) -> list[RestrictedZone]:
    """Все ограничения участка и нормативные полосы запрета посадки вокруг
    них (табличные нормы; строже по породе -- в объяснении конкретной
    посадки и в отклонённых)."""
    out = []
    for zone in scene.restrictions:
        if zone.severity == "allowed" or len(zone.polygon) < 3:
            continue
        tree, tree_norm = setback_basis(zone.type, "tree", zone.minDistance)
        bush, bush_norm = setback_basis(zone.type, "bush", zone.minDistance)
        out.append(RestrictedZone(
            zone_id=zone.id, what=ZONE_TYPE_LABELS.get(zone.type, zone.name or zone.type),
            tree_setback_m=tree, tree_norm=tree_norm, bush_setback_m=bush, bush_norm=bush_norm,
        ))
    return out


def explanations_json(scene: Scene, plants: list[PlantExplanation], rejections: RejectionStats) -> dict:
    return {
        "site": {
            "source_id": scene.meta.sourceId,
            "scale_m_per_unit": scene.meta.scale,
            "origin": scene.meta.origin,
            "coordinates": "x, z — метры от центра участка; dxf_x, dxf_y — координаты исходного чертежа",
        },
        "summary": {
            "plants": len(plants),
            "by_species": dict(Counter(p.species for p in plants).most_common()),
            "rejected_total": rejections.total,
            "rejected_by_rule": rejections.by_rule,
        },
        "plants": [p.model_dump() for p in plants],
        "rejected": [r.model_dump() for r in rejections.detailed],
        "restricted_zones": [z.model_dump() for z in restricted_zones(scene)],
    }


CSV_COLUMNS = [
    "id", "статус", "тип", "вид", "слой DXF", "x", "z", "dxf_x", "dxf_y", "приём", "проект-аналог",
    "обоснование", "основание вида", "ограничение", "расстояние, м", "норма, м", "НПА и пункт",
]


def explanations_csv(scene: Scene, plants: list[PlantExplanation], rejections: RejectionStats) -> str:
    """Одна строка на посадку (с ближайшим ограничением) и на каждый
    подробно разобранный отклонённый кандидат. ";" -- для Excel с русской
    локалью."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(CSV_COLUMNS)
    for p in plants:
        c = p.constraints[0] if p.constraints else None
        writer.writerow([
            p.id, p.status, p.type, p.species, p.dxf_layer, p.x, p.z, p.dxf_x, p.dxf_y, p.pattern or "", p.source_project or "",
            p.reason, p.species_basis, c.what if c else "", c.distance_m if c else "", c.required_m if c else "", c.norm if c else "",
        ])
    for r in rejections.detailed:
        writer.writerow([
            "", "отклонено", r.type, r.species, "", r.x, r.z, "", "", r.pattern or "", "",
            r.reason, "", "", r.distance_m if r.distance_m is not None else "",
            r.required_m if r.required_m is not None else "", r.norm,
        ])
    for z in restricted_zones(scene):
        writer.writerow([
            z.zone_id, "запрет посадки", "дерево", "", "", "", "", "", "", "", "",
            f"полоса вокруг объекта «{z.what}», где деревья не ставятся", "", z.what, "", z.tree_setback_m, z.tree_norm,
        ])
        writer.writerow([
            z.zone_id, "запрет посадки", "кустарник", "", "", "", "", "", "", "", "",
            f"полоса вокруг объекта «{z.what}», где кустарники не ставятся", "", z.what, "", z.bush_setback_m, z.bush_norm,
        ])
    return buf.getvalue()
