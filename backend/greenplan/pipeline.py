"""
Запуск GreenPlan целиком с параметрами пользователя (greenplan/options.py) --
общий для эндпоинта /api/greenplan/generate и тестов:

1. убрать результат прошлого запуска GreenPlan (посадки, благоустройство,
   газон) -- повторный запуск с другими параметрами заменяет его, а не
   сажает второй слой поверх; если задано -- и существующие деревья и кусты
   с нарушением норм (их место засаживается заново);
2. благоустройство (дорожки, фонари, скамейки) -- до посадок, чтобы посадки
   соблюдали отступы от него;
3. посадки (deterministic_placement.plan_site) -- стиль и предпочтительные
   виды из параметров;
4. газон -- после посадок (клумбы кустарника вычитаются), если включён.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from core.plant_catalog import CatalogItem, catalog_by_id, load_catalog
from core.schemas import Scene, SceneObject
from greenplan.deterministic_placement import plan_site
from greenplan.explanations import RejectionStats, start_rejection_log, stop_rejection_log
from greenplan.improvements import SOURCE, Improvements, is_greenplan_zone, plan_improvements
from greenplan.lawn import plan_lawns
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.violation_report import ZONE_TYPE_LABELS, find_violations


@dataclass
class GreenPlanRun:
    scene: Scene  # исходная сцена + благоустройство + посадки + газон
    new_plants: list[SceneObject]
    improvements: Improvements
    assignments: list[ZoneAssignment]
    notes: list[str] = field(default_factory=list)
    # Точки, которые расстановка рассматривала, но отклонила по нормам, --
    # с причиной и ссылкой на НПА (greenplan/explanations.py).
    rejections: RejectionStats = field(default_factory=RejectionStats)
    # Существующие деревья и кусты, убранные из-за нарушения норм
    # (options.remove_violating_plants).
    removed_plants: list[SceneObject] = field(default_factory=list)


def is_greenplan_object(obj: SceneObject) -> bool:
    # pattern_id -- у посадок, сделанных до появления метки source.
    return obj.metadata.get("source") == SOURCE or (bool(obj.metadata.get("generated")) and "pattern_id" in obj.metadata)


def without_greenplan(scene: Scene) -> Scene:
    """Сцена без результата GreenPlan: его посадок и МАФ, новых дорожек и газона."""
    return scene.model_copy(
        update={
            "objects": [o for o in scene.objects if not is_greenplan_object(o)],
            "restrictions": [z for z in scene.restrictions if not is_greenplan_zone(z)],
            "lawns": [],
        }
    )


def run_greenplan(scene: Scene, options: GreenPlanOptions | None = None, k: int = 3) -> GreenPlanRun:
    options = options or GreenPlanOptions()
    catalog = load_catalog()
    by_id = catalog_by_id()
    base = without_greenplan(scene)
    removed: list[SceneObject] = []
    removal_notes: list[str] = []
    if options.remove_violating_plants:
        base, removed, removal_notes = remove_violating_plants(base)

    improvements = plan_improvements(base, by_id, options)
    working = base.model_copy(
        update={
            "restrictions": [*base.restrictions, *improvements.zones],
            "objects": [*base.objects, *improvements.objects],
        }
    )

    trees = [c for c in catalog if c.category == "tree"] if options.trees else []
    bushes = [c for c in catalog if c.category == "bush" and c.object_type == "bush"] if options.bushes else []
    preferred, notes = _preferred(options, by_id)
    log, token = start_rejection_log()
    try:
        plan = plan_site(working, trees, bushes, k, style=options.style, preferred=preferred)
    finally:
        stop_rejection_log(token)

    final = working.model_copy(update={"objects": [*working.objects, *plan.objects]})
    final.lawns = plan_lawns(final, by_id) if options.lawn else []
    return GreenPlanRun(
        scene=final,
        new_plants=plan.objects,
        improvements=improvements,
        assignments=plan.assignments,
        notes=[*removal_notes, *improvements.notes, *notes, *plan.notes],
        rejections=log.stats(),
        removed_plants=removed,
    )


_PLANT_NAMES = {"tree": "деревьев", "bush": "кустарников"}


def remove_violating_plants(scene: Scene) -> tuple[Scene, list[SceneObject], list[str]]:
    """(сцена без существующих деревьев и кустов с нарушением норм, убранные,
    заметка: что убрано и почему). Нарушения -- те же, что в отчёте
    violation_report и в подсветке редактора; сюда приходит сцена уже без
    результата прошлого запуска GreenPlan, так что убираются только исходные."""
    reasons: dict[str, set[str]] = defaultdict(set)
    for v in find_violations(scene):
        if v.object_type in _PLANT_NAMES:
            reasons[v.object_id].add(ZONE_TYPE_LABELS.get(v.zone_type, v.zone_type))
    if not reasons:
        return scene, [], ["Существующих деревьев и кустов с нарушением норм нет — удалять нечего"]

    removed = [o for o in scene.objects if o.id in reasons]
    kept = scene.model_copy(update={"objects": [o for o in scene.objects if o.id not in reasons]})
    counts = Counter(o.type for o in removed)
    # По типу зоны, а не по каждой посадке: на реальном участке их сотни
    # (Харьковская -- 520 из 1223), а типов зон -- около десятка.
    by_zone = Counter(label for o in removed for label in reasons[o.id])
    totals = ", ".join(f"{_PLANT_NAMES[kind]} — {counts[kind]}" for kind in _PLANT_NAMES if counts[kind])
    why = "; ".join(f"{label} — {n}" for label, n in by_zone.most_common())
    return kept, removed, [f"Удалены существующие насаждения с нарушением норм ({totals}). Нарушены отступы: {why}"]


def _preferred(options: GreenPlanOptions, by_id: dict[str, CatalogItem]) -> tuple[list[CatalogItem], list[str]]:
    items: list[CatalogItem] = []
    notes: list[str] = []
    for ids, category, enabled, title in (
        (options.preferred_trees, "tree", options.trees, "деревья"),
        (options.preferred_bushes, "bush", options.bushes, "кустарники"),
    ):
        if ids and not enabled:
            notes.append(f"{title} выключены — предпочтительные {title} не используются")
            continue
        for catalog_id in ids:
            item = by_id.get(catalog_id)
            if item is None or item.category != category:
                notes.append(f"{catalog_id}: нет такого вида в каталоге ({title})")
            else:
                items.append(item)
    return items, notes
