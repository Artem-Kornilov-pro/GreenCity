"""
Каталог посадок и элементов благоустройства -- единый для генератора,
GreenPlan, ИИ-ассистента и фронтенда.

Запись описывает и смысл (категория, габариты, вид отступа), и внешний вид
(3D-модель; если её нет -- примитив по полю render). Новые виды добавляются
записями, без правок кода.

Категории: tree, bush, groundcover (газон, цветник), paving (мощение),
furniture (малые формы). Сквер -- не объект, а композиция из них.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from core.invasive_species import invasive_match
from core.paths import BACKEND_DIR

Category = Literal["tree", "bush", "groundcover", "paving", "furniture"]

# Форма примитива-заглушки на фронтенде, пока нет .glb-модели.
RenderShape = Literal["cone", "cluster", "pine", "sphere", "box", "patch", "bench", "lamp", "trash", "fountain"]


class CatalogItemDimensions(BaseModel):
    """Габариты, м."""

    height: float
    radius: Optional[float] = None       # радиус кроны/куста
    trunk_height: Optional[float] = None  # высота ствола до кроны (деревья)
    width: Optional[float] = None         # габарит по X (сегменты, МАФ)
    depth: Optional[float] = None         # габарит по Z (сегменты, МАФ)


class CatalogItemRender(BaseModel):
    shape: RenderShape
    color: str


# Класс размера и формы кроны -- по геометрии модели.
SizeClass = Literal["low", "medium", "tall"]
CrownClass = Literal["columnar", "regular", "spreading"]


class CatalogItem(BaseModel):
    """Вид растения, покрытие или малая форма."""

    id: str
    category: Category
    label: str
    size_class: Optional[SizeClass] = None
    crown_class: Optional[CrownClass] = None
    # Какую строку таблицы отступов применять; None -- малые формы и мощение.
    setback_kind: Optional[Literal["tree", "bush"]] = None
    object_type: str  # SceneObject.type
    model: str
    dimensions: CatalogItemDimensions
    render: CatalogItemRender


CATALOG: list[CatalogItem] = [
    # Деревья -- конкретные виды из catalog_generated.json.
    # --- Кустарники --------------------------------------------------------
    CatalogItem(
        id="bush_medium", size_class="low", category="bush", label="Кустарник — средний",
        setback_kind="bush", object_type="bush", model="/models/bush_medium.glb",
        dimensions=CatalogItemDimensions(height=1.0, radius=0.5),
        render=CatalogItemRender(shape="sphere", color="#4a9450"),
    ),
    CatalogItem(
        id="bush_tall", size_class="low", category="bush", label="Кустарник — высокий",
        setback_kind="bush", object_type="bush", model="/models/bush_tall.glb",
        dimensions=CatalogItemDimensions(height=1.5, radius=0.75),
        render=CatalogItemRender(shape="sphere", color="#3f8a46"),
    ),
    CatalogItem(
        id="bush_short", size_class="low", category="bush", label="Кустарник — низкий",
        setback_kind="bush", object_type="bush", model="/models/bush_short.glb",
        dimensions=CatalogItemDimensions(height=0.6, radius=0.32),
        render=CatalogItemRender(shape="sphere", color="#5aa15f"),
    ),
    CatalogItem(
        id="hedge_segment", category="bush", label="Живая изгородь (секция 2 м)",
        setback_kind="bush", object_type="hedge_segment", model="/models/hedge_segment.glb",
        dimensions=CatalogItemDimensions(height=0.9, width=2.0, depth=0.6),
        render=CatalogItemRender(shape="box", color="#4a7a44"),
    ),
    # --- Травяные покрытия -------------------------------------------------
    CatalogItem(
        id="lawn_patch", category="groundcover", label="Газон (участок 4×4 м)",
        setback_kind=None, object_type="lawn_patch", model="/models/lawn_patch.glb",
        dimensions=CatalogItemDimensions(height=0.05, width=4.0, depth=4.0),
        render=CatalogItemRender(shape="patch", color="#6f9e57"),
    ),
    CatalogItem(
        id="flowerbed_patch", category="groundcover", label="Цветник (участок 2×2 м)",
        setback_kind=None, object_type="flowerbed_patch", model="/models/flowerbed_patch.glb",
        dimensions=CatalogItemDimensions(height=0.25, width=2.0, depth=2.0),
        render=CatalogItemRender(shape="patch", color="#b5628f"),
    ),
    # --- Мощение -----------------------------------------------------------
    CatalogItem(
        id="path_segment", category="paving", label="Дорожка (сегмент 2×1.2 м)",
        setback_kind=None, object_type="path_segment", model="/models/path_segment.glb",
        dimensions=CatalogItemDimensions(height=0.06, width=2.0, depth=1.2),
        render=CatalogItemRender(shape="patch", color="#b7ada0"),
    ),
    # Разметка пешеходного перехода (ГОСТ Р 51256, 1.14.1).
    CatalogItem(
        id="crosswalk", category="paving", label="Пешеходный переход (разметка)",
        setback_kind=None, object_type="crosswalk", model="/models/crosswalk.glb",
        dimensions=CatalogItemDimensions(height=0.02, width=4.0, depth=2.0),
        render=CatalogItemRender(shape="patch", color="#e8e8e0"),
    ),
    # --- МАФ ---------------------------------------------------------------
    CatalogItem(
        id="bench", category="furniture", label="Лавка",
        setback_kind=None, object_type="bench", model="/models/bench.glb",
        dimensions=CatalogItemDimensions(height=0.4, width=1.4, depth=0.5),
        render=CatalogItemRender(shape="bench", color="#7a5230"),
    ),
    CatalogItem(
        id="lamp", category="furniture", label="Фонарь",
        setback_kind=None, object_type="lamp", model="/models/lamp.glb",
        dimensions=CatalogItemDimensions(height=3.2, radius=0.2),
        render=CatalogItemRender(shape="lamp", color="#f5e28a"),
    ),
    CatalogItem(
        id="trash", category="furniture", label="Урна",
        setback_kind=None, object_type="trash", model="/models/trash.glb",
        dimensions=CatalogItemDimensions(height=0.7, radius=0.26),
        render=CatalogItemRender(shape="trash", color="#3d4a3d"),
    ),
    CatalogItem(
        id="fountain", category="furniture", label="Фонтан",
        setback_kind=None, object_type="fountain", model="/models/fountain.glb",
        dimensions=CatalogItemDimensions(height=1.2, radius=1.2),
        render=CatalogItemRender(shape="fountain", color="#9aa0a6"),
    ),
]

# Виды растений с моделями: генерирует tools/convert_models.mjs.
_GENERATED_PATH = BACKEND_DIR / "catalog_generated.json"


def _load_generated() -> list[CatalogItem]:
    if not _GENERATED_PATH.exists():
        return []
    try:
        raw = json.loads(_GENERATED_PATH.read_text(encoding="utf-8"))
        return [CatalogItem(**entry) for entry in raw]
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"[plant_catalog] catalog_generated.json проигнорирован: {e}")
        return []


def load_catalog() -> list[CatalogItem]:
    """Базовый каталог и виды с моделями, без инвазивных видов из перечня
    ППМ 369-ПП. Файл перечитывается на каждый вызов: новый пак моделей
    виден без перезапуска."""
    return [
        item
        for item in (*CATALOG, *_load_generated())
        if item.setback_kind is None or invasive_match(item.label) is None
    ]


def catalog_by_id() -> dict[str, CatalogItem]:
    return {item.id: item for item in load_catalog()}


def items_by_category(category: Category) -> list[CatalogItem]:
    return [item for item in load_catalog() if item.category == category]
