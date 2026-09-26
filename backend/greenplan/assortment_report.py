"""
Сведение в ассортиментную ведомость -- GreenPlan, Этап 5 (issue #23:
"Сведение количеств по видам в структуру ассортиментной ведомости").

Считает только то, что GreenPlan реально сгенерировал сам
(metadata.generated -- см. deterministic_placement._scene_object), а не
сверяет с реальной ведомостью заказчика -- такой ведомости у нас нет, на
вход сейчас подаётся только DXF (см. обсуждение issue #23, Этап 2 по той же
причине не в объёме). Категории -- дерево/кустарник; "многолетник"/
"луковичное" из формулировки issue сейчас взять неоткуда: в plant_catalog.py
таких видов нет, GreenPlan генерирует только tree/bush.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from core.plant_catalog import CatalogItem
from core.schemas import LawnArea, Scene, SceneObject
from core.shapes import polygon_from_points
from greenplan.improvements import SOURCE, is_greenplan_zone

CATEGORY_LABELS: dict[str, str] = {
    "tree": "дерево",
    "bush": "кустарник",
}


class AssortmentRow(BaseModel):
    category: str
    species: str
    count: int
    # "шт." у деревьев и кустарников, "м²" у газона (ведомость по форме 9
    # ГОСТ 21.508 так и считает газон -- площадью).
    unit: str = "шт."


def summarize_assortment(objects: list[SceneObject], catalog: dict[str, CatalogItem]) -> list[AssortmentRow]:
    """Только объекты с metadata.generated=True -- то, что сгенерировал сам
    GreenPlan (deterministic_placement.generate_for_scene), не то, что уже
    было на участке. Объект без catalogId в переданном каталоге -- пропущен,
    а не падение: это защитный случай (сейчас deterministic_placement.py
    всегда проставляет catalogId для своих объектов), не ожидаемый путь."""
    counts: Counter[tuple[str, str]] = Counter()
    for obj in objects:
        if not obj.metadata.get("generated"):
            continue
        item = catalog.get(obj.metadata.get("catalogId"))
        # Только растения: фонари и скамейки благоустройства -- в своей
        # ведомости (improvements_assortment), а не в форме 9.
        if item is None or item.category not in CATEGORY_LABELS:
            continue
        category = CATEGORY_LABELS[item.category]
        counts[(category, item.label)] += 1

    rows = [AssortmentRow(category=category, species=species, count=count) for (category, species), count in counts.items()]
    rows.sort(key=lambda r: (r.category, -r.count))
    return rows


def lawn_assortment(lawns: list[LawnArea]) -> list[AssortmentRow]:
    """Строка ведомости для газона: только новый газон (устройство на
    открытой земле) -- существующий сохраняется и в объём работ не входит."""
    new_area = round(sum(a.area_sqm for a in lawns if a.status == "new"))
    if new_area <= 0:
        return []
    kind = next((a.kind for a in lawns if a.status == "new"), "Газон обыкновенный")
    return [AssortmentRow(category="газон", species=kind, count=new_area, unit="м²")]


IMPROVEMENT_LABELS: dict[str, str] = {"lamp": "Фонарь", "bench": "Скамейка", "trash": "Урна"}


def improvements_assortment(scene: Scene) -> list[AssortmentRow]:
    """Благоустройство GreenPlan (greenplan/improvements.py): площадь новых
    дорожек и число фонарей, скамеек и урн -- по самой сцене, чтобы записка
    считала так же, как ответ /generate."""
    rows = []
    paths = [polygon_from_points(z.polygon) for z in scene.restrictions if is_greenplan_zone(z)]
    area = round(sum(p.area for p in paths if p is not None))
    if area:
        rows.append(AssortmentRow(category="благоустройство", species="Дорожка (новая)", count=area, unit="м²"))
    counts = Counter(o.type for o in scene.objects if o.metadata.get("source") == SOURCE and o.type in IMPROVEMENT_LABELS)
    rows += [
        AssortmentRow(category="благоустройство", species=IMPROVEMENT_LABELS[t], count=counts[t])
        for t in IMPROVEMENT_LABELS
        if counts[t]
    ]
    return rows
