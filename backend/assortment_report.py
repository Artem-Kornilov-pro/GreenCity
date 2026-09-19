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

from plant_catalog import CatalogItem
from pydantic import BaseModel
from schemas import SceneObject

CATEGORY_LABELS: dict[str, str] = {
    "tree": "дерево",
    "bush": "кустарник",
}


class AssortmentRow(BaseModel):
    category: str
    species: str
    count: int


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
        if item is None:
            continue
        category = CATEGORY_LABELS.get(item.category, item.category)
        counts[(category, item.label)] += 1

    rows = [AssortmentRow(category=category, species=species, count=count) for (category, species), count in counts.items()]
    rows.sort(key=lambda r: (r.category, -r.count))
    return rows
