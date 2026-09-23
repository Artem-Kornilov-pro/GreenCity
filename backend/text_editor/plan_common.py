"""
Общие константы и вспомогательные функции применения плана правки текстом
(text_editor/applier.py и его части text_editor/ops_placement.py/plan_ops_editing.py):
шаги посадки по габаритам вида, подписи для сообщений пользователю,
нормализация операций от модели.
"""

from __future__ import annotations

from typing import Optional

from core.placement import (
    MAX_SPACING_M,
    MIN_SPACING_M,
    clamp,
)
from core.plant_catalog import CatalogItem

# Шаг по умолчанию для МАФ в ряду (лавки вдоль дорожки и т.п.).
FURNITURE_SPACING_M = {"bench": 10.0, "lamp": 15.0, "trash": 20.0, "fountain": 20.0}
# Повороты "золотым углом": одинаковые модели в ряду не смотрят в одну сторону.
GOLDEN_ANGLE_DEG = 137.5
DEFAULT_REMOVE_DISTANCE_M = 3.0
DEFAULT_ALONG_RADIUS_M = 15.0
DEFAULT_REMOVE_RADIUS_M = 10.0
# Кандидатов на одно место при равномерной посадке по области и всего -- для
# компактной группы вокруг точки.
CANDIDATES_PER_PLACEMENT = 12
CANDIDATES_NEAR_POINT = 2000
ALIGN_MATCH_REACH_M = 15.0  # align_along: искать сбившиеся объекты не дальше этого от цели
MIN_SCALE, MAX_SCALE = 0.3, 3.0

# --- Применение ---------------------------------------------------------------


def _normalize(raw: dict) -> dict:
    """Мелкие вольности модели, которые проще поправить, чем отклонять
    операцию: строка вместо списка, catalog_id вместо catalog_ids, null у
    необязательного поля со значением по умолчанию (например style: null) --
    для необязательных полей это то же самое, что их не прислать, но pydantic
    null и "отсутствует" не путает: явный null проходит мимо default и падает
    на полях без Optional (см. design_area: style: null отклонял всю
    операцию, хотя auto -- и так поведение по умолчанию)."""
    op = {k: v for k, v in raw.items() if v is not None}
    kind = op.get("op")
    if kind in ("place_along", "place_in_area") and "catalog_ids" not in op and "catalog_id" in op:
        op["catalog_ids"] = op.pop("catalog_id")
    if kind == "remove_where" and "object_types" not in op and "object_type" in op:
        op["object_types"] = op.pop("object_type")
    for key in ("catalog_ids", "object_types", "elements", "tree_ids", "bush_ids"):
        if isinstance(op.get(key), str):
            op[key] = [op[key]]
    return op


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    if n % 10 == 1 and n % 100 != 11:
        form = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{n} {form}"


_OBJECT_FORMS = ("объект", "объекта", "объектов")


def _labels(items: list[CatalogItem]) -> str:
    # Подписи моделей пака сами содержат запятые ("высокое, раскидистое"),
    # поэтому разделитель -- точка с запятой.
    names = [item.label for item in items]
    if len(names) > 3:
        return f"{'; '.join(names[:3])} и ещё {len(names) - 3}"
    return "; ".join(names)


def _pool_kind(items: list[CatalogItem]) -> Optional[str]:
    """Вид отступов для набора видов в одной операции -- самый строгий."""
    kinds = {item.setback_kind for item in items}
    if "tree" in kinds:
        return "tree"
    if "bush" in kinds:
        return "bush"
    return None


def _pool_species(items: list[CatalogItem]) -> list[str]:
    """Все виды набора: на любое место ряда/группы может встать любой из
    них, поэтому отступ -- по самому строгому (setback_norms.SpeciesArg)."""
    return [item.label for item in items]


def _default_spacing(item: CatalogItem) -> float:
    """Шаг посадки в ряду по габаритам вида: кроны соседей смыкаются, но не
    наезжают; секции изгороди, мощения и газона идут встык."""
    dims = item.dimensions
    if item.category == "tree":
        return max(5.0, 2 * (dims.radius or 1.0) + 2.0)
    if item.object_type in FURNITURE_SPACING_M:
        return FURNITURE_SPACING_M[item.object_type]
    if dims.width:
        return dims.width
    return max(1.2, 2 * (dims.radius or 0.5) + 0.4)


def _spacing_for(requested: Optional[float], items: list[CatalogItem]) -> float:
    default = max(_default_spacing(item) for item in items)
    return clamp(requested or default, MIN_SPACING_M, MAX_SPACING_M)


def _half_depth(item: CatalogItem) -> float:
    dims = item.dimensions
    if dims.radius:
        return dims.radius
    if dims.depth:
        return dims.depth / 2
    return 0.5


def _is_oriented(item: CatalogItem) -> bool:
    """Вытянутый объект (изгородь, лавка): в ряду его надо развернуть вдоль линии."""
    return item.dimensions.radius is None and item.dimensions.width is not None
