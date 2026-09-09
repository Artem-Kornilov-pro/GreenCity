"""
Каталог типовых посадок и элементов благоустройства -- единый источник правды
для всей системы (ТЗ: "каталоги типовых посадок с нормами по отступам").

Одна запись описывает и СЕМАНТИКУ, и ВНЕШНИЙ ВИД:

* семантика (category, setback_kind, dimensions) нужна генератору и будущему
  LLM/MCP-агенту: по ней он понимает, что можно ставить, какой у объекта
  габарит и какой нормативный отступ к нему применять;
* внешний вид (model, render) нужен фронтенду: если .glb-файл по пути `model`
  лежит в frontend/public -- рисуется он, если нет -- рисуется примитив по
  блоку `render`. Поэтому добавление новых видов (например, готового пака на
  200 деревьев) = дописать записи сюда и положить файлы, без правок кода
  фронтенда и бэкенда.

Категории (по ТЗ -- "деревья, кустарники, травянистые покрытия, скверы и др."):
    tree        -- дерево, точечная посадка
    bush        -- кустарник, точечная посадка
    groundcover -- травяное покрытие/газон, укладывается участками
    paving      -- мощение дорожек, укладывается сегментами
    furniture   -- МАФ: лавка, фонарь, урна, фонтан

"Сквер" сознательно НЕ отдельный тип объекта: это композиция из посадок,
дорожек и МАФ -- собирать её и есть задача генератора/агента, а не один
объект-заглушка в каталоге.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

Category = Literal["tree", "bush", "groundcover", "paving", "furniture"]

# Форма примитива-заглушки на фронтенде, пока нет .glb-модели.
RenderShape = Literal["cone", "cluster", "pine", "sphere", "box", "patch", "bench", "lamp", "trash", "fountain"]


class CatalogItemDimensions(BaseModel):
    """Габариты в метрах. Нужны и рендеру (размер примитива), и генератору
    (шаг посадки, проверка влезания в свободную площадь)."""

    height: float
    radius: Optional[float] = None       # радиус кроны/куста
    trunk_height: Optional[float] = None  # высота ствола до кроны (деревья)
    width: Optional[float] = None         # габарит по X (сегменты, МАФ)
    depth: Optional[float] = None         # габарит по Z (сегменты, МАФ)


class CatalogItemRender(BaseModel):
    shape: RenderShape
    color: str


# Характеристики формы. Выводятся из геометрии модели (высота и отношение
# высоты к ширине кроны), а не из названия породы: готовые паки пород не
# указывают. Нужны LLM-агенту, чтобы выбирать осмысленно ("низкое раскидистое
# под окна", "колонновидное вдоль дорожки"), а не наугад по номеру модели.
SizeClass = Literal["low", "medium", "tall"]
CrownClass = Literal["columnar", "regular", "spreading"]


class CatalogItem(BaseModel):
    id: str
    category: Category
    label: str
    size_class: Optional[SizeClass] = None
    crown_class: Optional[CrownClass] = None
    # Вид посадки для таблицы нормативных отступов (setback_norms.py).
    # None -- к объекту нормы отступов для растений неприменимы (МАФ, мощение).
    setback_kind: Optional[Literal["tree", "bush"]] = None
    # Тип объекта сцены (SceneObject.type) -- как он поедет в JSON и в DXF.
    object_type: str
    model: str
    dimensions: CatalogItemDimensions
    render: CatalogItemRender


CATALOG: list[CatalogItem] = [
    # --- Деревья -----------------------------------------------------------
    CatalogItem(
        id="tree_medium", size_class="medium", crown_class="regular", category="tree", label="Дерево — среднее",
        setback_kind="tree", object_type="tree", model="/models/tree_medium.glb",
        dimensions=CatalogItemDimensions(height=3.0, radius=0.9, trunk_height=1.2),
        render=CatalogItemRender(shape="cone", color="#2e7d3a"),
    ),
    CatalogItem(
        id="tree_tall", size_class="medium", crown_class="regular", category="tree", label="Дерево — высокое",
        setback_kind="tree", object_type="tree", model="/models/tree_tall.glb",
        dimensions=CatalogItemDimensions(height=4.6, radius=1.1, trunk_height=2.0),
        render=CatalogItemRender(shape="cone", color="#2e7d3a"),
    ),
    CatalogItem(
        id="tree_short", size_class="low", crown_class="regular", category="tree", label="Дерево — низкое",
        setback_kind="tree", object_type="tree", model="/models/tree_short.glb",
        dimensions=CatalogItemDimensions(height=1.6, radius=0.6, trunk_height=0.6),
        render=CatalogItemRender(shape="cone", color="#3f9146"),
    ),
    CatalogItem(
        id="tree_pine", size_class="medium", crown_class="columnar", category="tree", label="Сосна (конусовая)",
        setback_kind="tree", object_type="tree", model="/models/tree_pine.glb",
        dimensions=CatalogItemDimensions(height=4.6, radius=0.7, trunk_height=1.4),
        render=CatalogItemRender(shape="pine", color="#1f5c33"),
    ),
    CatalogItem(
        id="tree_round", size_class="low", crown_class="regular", category="tree", label="Дерево — круглая крона",
        setback_kind="tree", object_type="tree", model="/models/tree_round.glb",
        dimensions=CatalogItemDimensions(height=2.8, radius=0.85, trunk_height=1.2),
        render=CatalogItemRender(shape="cluster", color="#3a8f45"),
    ),
    CatalogItem(
        id="tree_round_large", size_class="medium", crown_class="regular", category="tree", label="Дерево — крупная круглая крона",
        setback_kind="tree", object_type="tree", model="/models/tree_round_large.glb",
        dimensions=CatalogItemDimensions(height=3.8, radius=1.35, trunk_height=1.6),
        render=CatalogItemRender(shape="cluster", color="#357f40"),
    ),
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

# Записи для моделей из готовых паков: их генерирует tools/convert_models.mjs
# при конвертации .obj -> .glb, чтобы 200 деревьев из пака появились в каталоге
# без ручной правки этого файла. Файла нет -- работаем на базовом наборе выше.
_GENERATED_PATH = Path(__file__).resolve().parent / "catalog_generated.json"


def _load_generated() -> list[CatalogItem]:
    if not _GENERATED_PATH.exists():
        return []
    try:
        raw = json.loads(_GENERATED_PATH.read_text(encoding="utf-8"))
        return [CatalogItem(**entry) for entry in raw]
    except (json.JSONDecodeError, ValidationError) as e:
        # Битый сгенерированный файл не должен ронять сервис: базовый каталог
        # самодостаточен, поэтому просто предупреждаем и работаем без пака.
        print(f"[plant_catalog] catalog_generated.json проигнорирован: {e}")
        return []


def load_catalog() -> list[CatalogItem]:
    """Базовый каталог + записи из сконвертированного пака моделей.

    Читается на каждый вызов, а не один раз при импорте: `uvicorn --reload`
    следит только за `.py`, поэтому появление catalog_generated.json иначе
    заметил бы лишь ручной перезапуск сервиса. Файл маленький, а эндпоинт
    дёргается редко -- перечитывать дешевле, чем требовать рестарт.
    """
    return [*CATALOG, *_load_generated()]


def catalog_by_id() -> dict[str, CatalogItem]:
    return {item.id: item for item in load_catalog()}


def items_by_category(category: Category) -> list[CatalogItem]:
    return [item for item in load_catalog() if item.category == category]
