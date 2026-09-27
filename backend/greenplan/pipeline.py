"""
Запуск GreenPlan целиком с параметрами пользователя (greenplan/options.py) --
общий для эндпоинта /api/greenplan/generate и тестов:

1. убрать результат прошлого запуска GreenPlan (посадки, благоустройство,
   газон) -- повторный запуск с другими параметрами заменяет его, а не
   сажает второй слой поверх;
2. благоустройство (дорожки, фонари, скамейки) -- до посадок, чтобы посадки
   соблюдали отступы от него;
3. посадки (deterministic_placement.plan_site) -- стиль и предпочтительные
   виды из параметров;
4. газон -- после посадок (клумбы кустарника вычитаются), если включён.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.plant_catalog import CatalogItem, catalog_by_id, load_catalog
from core.schemas import Scene, SceneObject
from greenplan.deterministic_placement import plan_site
from greenplan.explanations import RejectionStats, start_rejection_log, stop_rejection_log
from greenplan.improvements import SOURCE, Improvements, is_greenplan_zone, plan_improvements
from greenplan.lawn import plan_lawns
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import ZoneAssignment


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
        notes=[*improvements.notes, *notes, *plan.notes],
        rejections=log.stats(),
    )


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
