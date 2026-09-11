"""
Текстовое редактирование плана озеленения через LLM (ТЗ: "возможность внесения
корректировок в текстовом и графическом формате" -- графический формат это
3D-редактор, текстовый -- этот модуль).

LLM НЕ возвращает сцену целиком: сцена бывает огромной (локация 5 -- больше
тысячи объектов и 2000+ зон), такой JSON не влезает в ответ, а модель
гарантированно испортит в нём координаты. Модель возвращает короткий список
операций, а этот модуль применяет их детерминированно. Модель предлагает --
код решает. Этот же словарь операций ложится один в один на инструменты
будущего MCP-сервера.

Задача разбита на два уровня:
* групповые операции (place_along, place_in_area, remove_where) -- модель
  выбирает намерение, виды и параметры, а координаты считает геометрический
  планировщик (placement.py). Так выполняется всё про ряды, аллеи, "вдоль",
  "по периметру", "засадить";
* точечные операции (add, remove, move, rotate) -- для конкретных объектов.
  Точку с нарушением норм планировщик сдвигает в ближайшее допустимое место.

Почему так: в первой версии модель сама считала координаты каждого куста и на
"размести кустарники вдоль дорожек" поставила 5 кустов -- на дорожке, на
парковке, у стены и вплотную к фонарям, хотя контуры дорожек были в контексте
полностью. Смещённая линия, шаг и проверка сотни точек -- работа для кода, а
не для языковой модели.

Настройки берутся из переменных окружения (.env в корне репозитория локально,
env_file в docker-compose): YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER,
YANDEX_CLOUD_MODEL.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Optional

import openai
from courtyard_design import DEFAULT_ELEMENTS, DESIGN_ITEM_IDS, ELEMENTS, STYLES, CourtyardDesigner
from dotenv import load_dotenv
from placement import (
    FALLBACK_ROW_STEP_M,
    MAX_AREA_RADIUS_M,
    MAX_BULK_PLACEMENTS,
    MAX_SNAP_DISTANCE_M,
    MAX_SPACING_M,
    MIN_SPACING_M,
    TARGET_LABELS,
    Placer,
    PointIndex,
    clamp,
    pick_near,
    pick_spread,
    spread_subset,
)
from plant_catalog import CATALOG as BASE_CATALOG
from plant_catalog import CatalogItem, load_catalog
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from schemas import Point3, Scene, SceneObject
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

# Локально .env лежит в корне репозитория. В Docker переменные уже приходят из
# env_file, а load_dotenv без override существующие значения не перетирает.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
TEMPERATURE = 0.3
# С запасом под рассуждающие модели. Текущая yandexgpt не рассуждает и тратит на
# ответ ~150-250 токенов -- лимит её не замедляет. Но если переключить
# YANDEX_CLOUD_MODEL на рассуждающую (deepseek-v4-flash), рассуждение и ответ
# идут в один лимит: на 2000 ответ обрывался пустым (status=incomplete), на 8000
# обычно укладывался в ~3-4 тыс., но изредка не хватало и его. Поле
# reasoning_tokens у Yandex всегда 0 -- ориентироваться можно только на status.
MAX_OUTPUT_TOKENS = 8000

# Лимиты на размер контекста: каждый символ -- токены и деньги на каждый
# запрос, а на районе (локация 5) полные списки зданий и объектов давали
# ~67 тыс. символов. Потеря точности допустима: координаты групповых посадок
# считает планировщик по полной геометрии сцены, а не по промпту.
MAX_OBJECTS_IN_PROMPT = 120
MAX_BUILDINGS_IN_PROMPT = 40

# Сколько моделей из пака показывать на каждый класс формы (категория x размер
# x крона). Полный пак на 200 деревьев занимал 85% контекста; модели для выбора
# нужен класс формы, а не каждое из 200 почти одинаковых деревьев. Базовые
# позиции каталога (МАФ, газон, мощение) показываются все.
MAX_PACK_ITEMS_PER_SHAPE_CLASS = 6

# Контуры упрощаются, пока в них не больше стольких точек.
MAX_OUTLINE_POINTS = 40
FREE_AREA_OUTLINE_POINTS = 24

# Неизменяемые ориентиры: их нельзя двигать, но без них модели не понять
# просьбы вида "у входа" или "рядом с площадкой".
LANDMARK_TYPES = {"entrance": "подъезд", "playground": "детская площадка"}
MAX_LANDMARKS_IN_PROMPT = 60

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


class LlmNotConfiguredError(RuntimeError):
    """Не заданы ключи LLM -- сервис работает, но текстовые правки недоступны."""


class LlmError(RuntimeError):
    """LLM недоступна или вернула ответ, который не удалось разобрать."""


# --- Операции, которые может вернуть модель ---------------------------------

Target = Literal["pedestrian_path", "road", "building", "parking", "playground", "site_boundary"]


class AddOp(BaseModel):
    op: Literal["add"]
    catalog_id: str
    x: float
    z: float
    rotation_deg: float = 0.0


class RemoveOp(BaseModel):
    op: Literal["remove"]
    id: str


class MoveOp(BaseModel):
    op: Literal["move"]
    id: str
    x: float
    z: float


class RotateOp(BaseModel):
    op: Literal["rotate"]
    id: str
    rotation_deg: float


class PlaceAlongOp(BaseModel):
    """Ряд посадок вдоль контура цели, с обеих сторон."""

    op: Literal["place_along"]
    target: Target
    catalog_ids: list[str]
    spacing_m: Optional[float] = None
    offset_m: Optional[float] = None
    max_count: Optional[int] = None
    # Ограничить ряд кругом (например, "вдоль дорожки у третьего подъезда").
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class PlaceInAreaOp(BaseModel):
    """Группа посадок: компактно вокруг точки или равномерно по области."""

    op: Literal["place_in_area"]
    catalog_ids: list[str]
    count: int
    spacing_m: Optional[float] = None
    area: Optional[str] = None  # id из free_areas контекста
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class RemoveWhereOp(BaseModel):
    """Удалить все объекты заданных типов, подходящие под фильтры."""

    op: Literal["remove_where"]
    object_types: list[str]
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class ConnectOp(BaseModel):
    """Проложить дорожку между двумя точками: подъезд-подъезд, подъезд-объект
    (например фонтан), сеть дорожек-зона (например парковка). Каждый конец --
    id объекта, целевая зона (target) или явные координаты; ровно один из
    трёх на каждый конец. design_area прокладывает целую сеть сама и уже
    сама подводит дорожки к подъездам и к парковке -- эта операция нужна для
    точечной связи, которую ни одна другая не строит: например к объекту,
    добавленному отдельно от дизайна двора."""

    op: Literal["connect"]
    from_id: Optional[str] = None
    from_target: Optional[Target] = None
    from_x: Optional[float] = None
    from_z: Optional[float] = None
    to_id: Optional[str] = None
    to_target: Optional[Target] = None
    to_x: Optional[float] = None
    to_z: Optional[float] = None


class DesignAreaOp(BaseModel):
    """Полный дизайн двора: каркас дорожек и благоустройство вокруг него."""

    op: Literal["design_area"]
    elements: list[str] = []  # пусто -- courtyard_design.DEFAULT_ELEMENTS
    # courtyard_design.STYLES; неизвестное значение и null (модели иногда
    # присылают style: null вместо того, чтобы просто не указывать поле) --
    # тоже auto, см. design_area().
    style: Optional[str] = "auto"
    tree_ids: list[str] = []
    bush_ids: list[str] = []
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


Operation = Annotated[
    AddOp | RemoveOp | MoveOp | RotateOp | PlaceAlongOp | PlaceInAreaOp | RemoveWhereOp | ConnectOp | DesignAreaOp,
    Field(discriminator="op"),
]
_OPERATION = TypeAdapter(Operation)


class LlmPlan(BaseModel):
    # Сырые словари, а не list[Operation]: одна кривая операция не должна
    # ронять весь план -- каждая разбирается отдельно в apply_plan().
    operations: list[dict] = []
    explanation: str = ""


class TextEditRequest(BaseModel):
    scene: Scene
    instruction: str = Field(min_length=1, max_length=2000)


class TextEditResult(BaseModel):
    scene: Scene
    explanation: str
    applied: list[str]
    rejected: list[str]
    warnings: list[str]


# --- Контекст для модели ----------------------------------------------------


def _outline(xz: list[tuple[float, float]], limit: int = MAX_OUTLINE_POINTS) -> list[list[float]]:
    """Контур полигона: упрощённый и округлённый до 0.5 м.

    Именно контур, а не bbox: здания и участки бывают повёрнуты. В локации 1
    дом -- полоса 218x13 м под углом, а её bbox -- квадрат ~190x126 м, почти
    целиком пустой. Получив bbox, модель ставила деревья "у стены" в точках,
    которые на деле лежали за границей участка.
    """
    poly = Polygon(xz)
    if poly.is_valid and poly.area > 0:
        tolerance = 0.5
        simple = poly.simplify(tolerance, preserve_topology=True)
        while len(simple.exterior.coords) - 1 > limit and tolerance < 64:
            tolerance *= 2
            simple = poly.simplify(tolerance, preserve_topology=True)
        xz = list(simple.exterior.coords)[:-1]  # без повторной замыкающей точки
    return [[round(x * 2) / 2, round(z * 2) / 2] for x, z in xz]


def _inner_center(poly: Polygon) -> list[float]:
    """Центр масс, если он внутри фигуры; у П-образного двора он снаружи --
    тогда гарантированно внутренняя точка."""
    point = poly.centroid if poly.contains(poly.centroid) else poly.representative_point()
    return [round(point.x * 2) / 2, round(point.y * 2) / 2]


def _editable_types(catalog: list[CatalogItem]) -> set[str]:
    return {item.object_type for item in catalog}


def _pack_substitutes(catalog: list[CatalogItem]) -> dict[str, CatalogItem]:
    """Базовое дерево-примитив -> модель из пака того же размера и кроны (или
    любая из пака). Деревья сажаем только из пака; базовые -- крайний случай,
    когда пак не сконвертирован. Пусто, если пака нет."""
    base_ids = {item.id for item in BASE_CATALOG}
    pack = [item for item in catalog if item.category == "tree" and item.id not in base_ids]
    if not pack:
        return {}
    substitutes = {}
    for item in catalog:
        if item.category == "tree" and item.id in base_ids:
            same = [p for p in pack if p.size_class == item.size_class and p.crown_class == item.crown_class]
            substitutes[item.id] = (same or pack)[0]
    return substitutes


def _catalog_for_prompt(catalog: list[CatalogItem]) -> list[list]:
    """Каталог таблицей (строки-массивы, заголовок -- в catalog_columns), а не
    списком словарей: повторяющиеся ключи в 217 записях и были основным
    объёмом. Модели из пака -- выборкой по классу формы, в порядке файла, чтобы
    выборка была стабильной от запроса к запросу."""
    base_ids = {item.id for item in BASE_CATALOG}
    hide_base_trees = bool(_pack_substitutes(catalog))
    per_class: dict[tuple, int] = {}
    rows: list[list] = []
    for item in catalog:
        is_base = item.id in base_ids
        if is_base and item.category == "tree" and hide_base_trees:
            continue
        if not is_base:
            key = (item.category, item.size_class, item.crown_class)
            if per_class.get(key, 0) >= MAX_PACK_ITEMS_PER_SHAPE_CLASS:
                continue
            per_class[key] = per_class.get(key, 0) + 1
        rows.append([
            item.id,
            item.category,
            item.size_class or "",
            item.crown_class or "",
            item.dimensions.height,
            # У базовых позиций подпись осмысленная ("Лавка", "Фонтан"); у
            # моделей пака она лишь пересказывает размер/крону/высоту.
            item.label if is_base else "",
        ])
    return rows


def _build_context(scene: Scene, catalog: list[CatalogItem], placer: Placer) -> str:
    editable = _editable_types(catalog)

    all_buildings = [o for o in scene.objects if o.type == "building"]
    buildings = []
    for obj in all_buildings[:MAX_BUILDINGS_IN_PROMPT]:
        footprint = obj.metadata.get("footprint") or []
        entry = {"name": obj.metadata.get("name", obj.id), "height_m": obj.metadata.get("height")}
        if len(footprint) >= 3:
            entry["outline"] = _outline([(p["x"], p["z"]) for p in footprint])
        buildings.append(entry)

    all_landmarks = [o for o in scene.objects if o.type in LANDMARK_TYPES]
    landmarks = [
        {"type": LANDMARK_TYPES[o.type], "x": round(o.position.x, 1), "z": round(o.position.z, 1)}
        for o in all_landmarks[:MAX_LANDMARKS_IN_PROMPT]
    ]

    # Модели не нужны контуры каждой трубы и дорожки: ряды считает
    # планировщик. Ей нужно знать, какие цели для рядов есть и насколько они
    # протяжённые, и где есть место для групп.
    targets = []
    for target, label in TARGET_LABELS.items():
        summary = placer.target_summary(target)
        if summary is not None:
            count, length = summary
            targets.append({"target": target, "label": label, "count": count, "outline_length_m": round(length)})

    free_areas = [
        {
            "id": f"A{number}",
            "area_m2": round(poly.area),
            "center": _inner_center(poly),
            "outline": _outline(list(poly.exterior.coords)[:-1], FREE_AREA_OUTLINE_POINTS),
        }
        for number, poly in enumerate(placer.free_areas(), 1)
    ]

    editable_objects = [o for o in scene.objects if o.type in editable]
    objects = [
        {
            "id": o.id,
            "type": o.type,
            "label": o.metadata.get("label", o.type),
            "x": round(o.position.x, 1),
            "z": round(o.position.z, 1),
            "rotation_deg": round(math.degrees(o.rotation)),
        }
        for o in editable_objects[:MAX_OBJECTS_IN_PROMPT]
    ]

    context = {
        "coordinates": "метры; X — запад→восток, Z — юг→север, начало — центр участка; контуры — точки [x, z]",
        "site_outline": _outline([(p.x, p.z) for p in scene.boundary.polygon]) if scene.boundary else None,
        "buildings": buildings,
        "buildings_not_shown": max(0, len(all_buildings) - MAX_BUILDINGS_IN_PROMPT),
        "landmarks": landmarks,
        "landmarks_not_shown": max(0, len(all_landmarks) - MAX_LANDMARKS_IN_PROMPT),
        "targets": targets,
        "free_areas": free_areas,
        "restriction_zones": dict(Counter(z.type for z in scene.restrictions if z.severity != "allowed")),
        "objects": objects,
        "objects_not_shown": max(0, len(editable_objects) - MAX_OBJECTS_IN_PROMPT),
        "object_counts": dict(Counter(o.type for o in editable_objects)),
        "catalog_columns": ["catalog_id", "category", "size", "crown", "height_m", "label"],
        "catalog_rows": _catalog_for_prompt(catalog),
    }
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))


INSTRUCTIONS = """Ты — ассистент ландшафтного архитектора. По текстовой просьбе пользователя ты составляешь правки плана озеленения участка в виде операций.

Координаты групповых посадок считает геометрический планировщик: он сам соблюдает нормативные отступы от зданий, подземных сетей, дорожек, парковок, площадок, фонарей и подъездов, выдерживает шаг посадки и не выходит за участок. Твоя задача — понять намерение и выбрать операции, виды из каталога и параметры. Координаты рядов и групп сам не считай.

Контекст (JSON): контур участка, здания, ориентиры (landmarks: подъезды, площадки), цели для рядов (targets), свободные для посадки области (free_areas), текущие объекты (objects) и каталог (catalog_rows, колонки описаны в catalog_columns). Координаты в метрах, контуры — точки [x, z].

Верни ТОЛЬКО валидный JSON без markdown, строго такой формы:
{"operations": [...], "explanation": "..."}

Групповые операции — для рядов, аллей, изгородей, «вдоль», «вокруг», «по периметру», «засадить», «много»:
- {"op": "place_along", "target": "<target из targets>", "catalog_ids": ["..."], "spacing_m": <необязательно>, "offset_m": <необязательно>, "max_count": <необязательно>, "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Ряд посадок вдоль контура цели с обеих сторон: pedestrian_path — вдоль дорожек, road — вдоль дорог, building — вдоль фасадов, parking — вокруг парковок, playground — вокруг детских площадок, site_boundary — по периметру участка. x, z, radius_m — только если ряд нужен в одном месте (например, у конкретного подъезда).
- {"op": "place_in_area", "catalog_ids": ["..."], "count": <сколько>, "spacing_m": <необязательно>, "area": "<id из free_areas, необязательно>", "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Группа посадок: с x и z — компактно вокруг точки; с area — равномерно по свободной области; без них — равномерно по всему участку.
- {"op": "remove_where", "object_types": ["<тип из objects>"], "target": "<необязательно>", "distance_m": <необязательно>, "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Удалить все объекты этих типов, подходящие под фильтры: у цели ближе distance_m (по умолчанию 3 м) и/или в радиусе от точки. Без фильтров — все объекты этих типов.
- {"op": "design_area", "elements": ["paths", "flowerbeds", "fountain", "lamps", "benches", "trash", "hedge", "trees", "bushes"], "style": "<необязательно>", "tree_ids": [<необязательно>], "bush_ids": [<необязательно>], "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Полный дизайн двора одной операцией: планировщик сам прокладывает каркас дорожек (от подъезда к подъезду, с выходом на парковку, если она рядом), расставляет фонари и скамейки с урнами вдоль дорожек, живую изгородь по краю двора, деревья вразброс по свободной площади. В elements перечисли то, что просили: paths — дорожки (прокладываются всегда), flowerbeds — клумбы, fountain — фонтан, lamps — фонари, benches — скамейки, trash — урны, hedge — живая изгородь, trees — деревья, bushes — кусты. Если просят «дизайн», «благоустройство», «сквер», «парк» без перечня — elements не указывай (по умолчанию — всё, КРОМЕ фонтана). fountain указывай, только если фонтан просят явно: площадь для него есть не в каждом дворе, и без явной просьбы он не ставится. style — шаблон каркаса дорожек: spine (дорожки от подъезда к подъезду — по умолчанию для обычного двора), diagonal (площадь на пересечении диагоналей — только если явно просят «крест», «по диагонали»), grid (сетка дорожек, для большого двора), perimeter (дорожка по периметру, для узкого двора); без явной просьбы про форму дорожек не указывай — планировщик сам подберёт по форме двора. x, z, radius_m — только если дизайн нужен в конкретной части участка.
- {"op": "connect", "from_id"/"from_target"/"from_x"+"from_z": "<одно из трёх>", "to_id"/"to_target"/"to_x"+"to_z": "<одно из трёх>"}
  Проложить дорожку между двумя точками: подъезд-подъезд, подъезд или другой объект (например фонтан) — id из objects; сеть дорожек или граница участка до зоны (например парковки) — target из targets. Для "от подъезда к Х" или "соедини сеть дорожек с Y" — эта операция, а не place_along. design_area уже сама тянет дорожки к подъездам и парковке — connect нужен для точечной, дополнительной связи.

Точечные операции — только для конкретных объектов или одной-двух посадок в названном месте:
- {"op": "add", "catalog_id": "...", "x": <число>, "z": <число>, "rotation_deg": <необязательно>}
- {"op": "remove", "id": "<id из objects>"}
- {"op": "move", "id": "<id из objects>", "x": <число>, "z": <число>}
- {"op": "rotate", "id": "<id из objects>", "rotation_deg": <число>}

Правила:
- catalog_id бери только из catalog_rows, id — только из objects, target — только из targets, area — только из free_areas. Не выдумывай.
- Деревья, кустарники и МАФ — разными операциями. Кустарники — строки с category "bush" (живая изгородь — только hedge_segment), деревья — "tree".
- В catalog_ids — один или несколько видов подходящего класса; одинаковые деревья сажать можно.
- spacing_m и offset_m не указывай, если пользователь не просит гуще, реже или дальше: шаг по размеру вида планировщик возьмёт сам.
- count и max_count — по числу из просьбы; «несколько» — 3–5. Для «вдоль», «по периметру», «засади» без числа max_count не указывай.
- Если в просьбе вместе дорожки, скамейки, урны, фонари, клумбы, изгородь или «благоустрой/спроектируй двор», «сделай сквер/парк» — ОДНА операция design_area, а не отдельные посадки.
- «У входа» — координаты подъезда из landmarks. «В центре двора» — center самой большой области из free_areas. «Вокруг фонтана» / «у скамейки» и т.п. — x, z существующего объекта нужного типа из objects (не landmark и не target).
- Здания, подъезды, дорожки и зоны менять нельзя.
- Если просьба невыполнима (например, нужной цели нет в targets) — пустой operations и причина в explanation.
- explanation — одно-два предложения по-русски: что сделано. Точное количество не называй: его посчитает планировщик.

Пример 1. Просьба: «посади кусты вдоль дорожек и два дерева у первого подъезда».
{"operations": [{"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium", "bush_tall", "bush_short"]}, {"op": "place_in_area", "catalog_ids": ["<catalog_id дерева из catalog_rows>"], "count": 2, "x": 12.5, "z": -30.0, "radius_m": 10}], "explanation": "Вдоль дорожек высажены кустарники, у первого подъезда — два дерева."}

Пример 2. Просьба: «добавь деревья вокруг фонтана», в objects есть {"id": "fountain_llm_a1b2c3d4", "type": "fountain", "x": 5.0, "z": -12.0, ...}.
{"operations": [{"op": "place_in_area", "catalog_ids": ["<catalog_id дерева>"], "count": 4, "x": 5.0, "z": -12.0, "radius_m": 8}], "explanation": "Вокруг фонтана посажены четыре дерева."}"""


# --- Вызов модели -----------------------------------------------------------


def _client_and_model() -> tuple[openai.OpenAI, str]:
    api_key = os.environ.get("YANDEX_CLOUD_API_KEY")
    folder = os.environ.get("YANDEX_CLOUD_FOLDER")
    model = os.environ.get("YANDEX_CLOUD_MODEL", "yandexgpt/latest")
    if not api_key or not folder:
        logger.warning("не заданы YANDEX_CLOUD_API_KEY / YANDEX_CLOUD_FOLDER")
        raise LlmNotConfiguredError(
            "Текстовое редактирование не настроено: задайте YANDEX_CLOUD_API_KEY и "
            "YANDEX_CLOUD_FOLDER (см. .env.example)."
        )
    client = openai.OpenAI(api_key=api_key, base_url=YANDEX_BASE_URL, project=folder)
    return client, f"gpt://{folder}/{model}"


def _extract_json(text: str) -> dict:
    """Модели нередко оборачивают JSON в ```json ... ``` или добавляют фразу
    перед ним, даже когда просили этого не делать. Берём от первой `{` до
    последней `}` -- этого достаточно для ответа с одним объектом верхнего
    уровня, которого мы и требуем."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LlmError(f"Модель не вернула JSON: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LlmError(f"Модель вернула некорректный JSON: {e}") from e


def request_plan(scene: Scene, instruction: str, catalog: list[CatalogItem], placer: Placer) -> LlmPlan:
    client, model = _client_and_model()
    context = _build_context(scene, catalog, placer)
    user_input = f"Контекст:\n{context}\n\nПросьба пользователя:\n{instruction}"
    logger.info("запрос: %r | контекст %d симв.", instruction[:200], len(context))

    started = time.monotonic()
    try:
        response = client.responses.create(
            model=model,
            temperature=TEMPERATURE,
            instructions=INSTRUCTIONS,
            input=user_input,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.OpenAIError as e:
        logger.warning("LLM недоступна через %.1f с: %s", time.monotonic() - started, e)
        raise LlmError(f"LLM недоступна: {e}") from e

    usage = response.usage
    logger.info(
        "ответ: status=%s за %.1f с | токены: вход %s, выход %s (лимит %d)",
        response.status,
        time.monotonic() - started,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        MAX_OUTPUT_TOKENS,
    )

    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        logger.warning("ответ оборван: %s", reason)
        if reason == "max_output_tokens":
            raise LlmError(
                "Модель не уложилась в лимит ответа -- рассуждала слишком долго. "
                "Попробуйте сформулировать просьбу проще или разбить на части."
            )
        raise LlmError(f"Модель не завершила ответ ({reason or 'причина неизвестна'})")

    text = response.output_text or ""
    try:
        return LlmPlan.model_validate(_extract_json(text))
    except LlmError:
        logger.warning("ответ не разобрать как JSON, начало: %r", text[:300])
        raise
    except ValidationError as e:
        logger.warning("план в неожиданном формате (%d ошибок), начало: %r", e.error_count(), text[:300])
        raise LlmError(f"Модель вернула план в неожиданном формате: {e.error_count()} ошибок") from e


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


class _PlanApplier:
    def __init__(self, scene: Scene, catalog: list[CatalogItem], placer: Placer):
        self.scene = scene
        self.catalog = catalog
        self.by_id = {item.id: item for item in catalog}
        # Если модель всё же назовёт базовое дерево -- сажаем похожее из пака.
        self.by_id.update(_pack_substitutes(catalog))
        self.editable = _editable_types(catalog)
        self.placer = placer
        self.objects = {o.id: o for o in scene.objects}
        self.applied: list[str] = []
        self.rejected: list[str] = []
        self.warnings: list[str] = []

    def run(self, plan: LlmPlan) -> TextEditResult:
        handlers = {
            AddOp: self.add,
            RemoveOp: self.remove,
            MoveOp: self.move,
            RotateOp: self.rotate,
            PlaceAlongOp: self.place_along,
            PlaceInAreaOp: self.place_in_area,
            RemoveWhereOp: self.remove_where,
            ConnectOp: self.connect,
            DesignAreaOp: self.design_area,
        }
        for number, raw in enumerate(plan.operations, 1):
            try:
                op = _OPERATION.validate_python(_normalize(raw))
            except ValidationError as e:
                error = e.errors()[0]
                # loc вида ("place_along", "target"): первый элемент -- тег операции.
                field = ".".join(str(part) for part in error["loc"][1:])
                detail = f"{field}: {error['msg']}" if field else error["msg"]
                self.rejected.append(f"операция {number} ({raw.get('op', '?')}): не разобрать — {detail}")
                continue
            handlers[type(op)](op)

        # Исходную сцену не трогаем: при ошибке посередине у фронтенда
        # остаётся прежняя.
        scene = self.scene.model_copy(update={"objects": list(self.objects.values())})
        return TextEditResult(
            scene=scene,
            explanation=plan.explanation,
            applied=self.applied,
            rejected=self.rejected,
            warnings=self.warnings,
        )

    # --- Общие шаги --------------------------------------------------------

    def _pool(self, catalog_ids: list[str], what: str) -> Optional[list[CatalogItem]]:
        items = []
        unknown = []
        for catalog_id in dict.fromkeys(catalog_ids):
            item = self.by_id.get(catalog_id)
            if item is None:
                unknown.append(catalog_id)
            else:
                items.append(item)
        if unknown:
            target = self.warnings if items else self.rejected
            target.append(f"{what}: нет в каталоге: {', '.join(unknown)}")
        return items or None

    def _create(self, item: CatalogItem, x: float, z: float, rotation_deg: float) -> None:
        new_id = f"{item.object_type}_llm_{uuid.uuid4().hex[:8]}"
        self.objects[new_id] = SceneObject(
            id=new_id,
            type=item.object_type,
            model=item.model,
            position=Point3(x=x, y=0.0, z=z),
            rotation=math.radians(rotation_deg),
            scale=1.0,
            metadata={"catalogId": item.id, "label": item.label, "source": "llm"},
        )
        self.placer.occupy(new_id, x, z, item.object_type)

    def _plant(self, items: list[CatalogItem], spots: list[tuple]) -> str:
        """Создать объекты в точках, чередуя виды; вернуть подписи видов."""
        for i, spot in enumerate(spots):
            item = items[i % len(items)]
            rotation = spot[2] if len(spot) > 2 and _is_oriented(item) else (i * GOLDEN_ANGLE_DEG) % 360
            self._create(item, spot[0], spot[1], rotation)
        return _labels(items[: len(spots)])

    def _spot(self, x: float, z: float, kind: Optional[str], action: str, what: str):
        """Точка для точечной операции: сама (x, z), если там можно, иначе
        ближайшая допустимая. None -- операция отклонена (причина записана)."""
        reason = None if self.placer.is_free(x, z, kind) else self.placer.explain(x, z, kind)
        spot = self.placer.nearest_free(x, z, kind)
        if spot is None:
            self.rejected.append(
                f"{action} в ({x:.1f}, {z:.1f}): {reason}; в радиусе {MAX_SNAP_DISTANCE_M:.0f} м нет места без нарушений"
            )
            return None
        shift = math.hypot(spot[0] - x, spot[1] - z)
        if reason is not None and shift > 0.05:
            self.warnings.append(f"{what}: сдвинуто на {shift:.1f} м, чтобы соблюсти норму: {reason}")
        return spot

    def _editable(self, action: str, obj_id: str) -> Optional[SceneObject]:
        obj = self.objects.get(obj_id)
        if obj is None:
            self.rejected.append(f"{action} {obj_id}: такого объекта нет")
            return None
        if obj.type not in self.editable:
            self.rejected.append(f"{action} {obj_id}: объект «{obj.type}» менять нельзя")
            return None
        return obj

    def _setback_kind(self, obj: SceneObject) -> Optional[str]:
        catalog_id = obj.metadata.get("catalogId")
        if catalog_id in self.by_id:
            return self.by_id[catalog_id].setback_kind
        for item in self.catalog:
            if item.object_type == obj.type:
                return item.setback_kind
        return None

    # --- Точечные операции -------------------------------------------------

    def add(self, op: AddOp) -> None:
        item = self.by_id.get(op.catalog_id)
        if item is None:
            self.rejected.append(f"add {op.catalog_id}: такого вида нет в каталоге")
            return
        spot = self._spot(op.x, op.z, item.setback_kind, f"add «{item.label}»", f"«{item.label}»")
        if spot is None:
            return
        self._create(item, spot[0], spot[1], op.rotation_deg)
        self.applied.append(f"добавлено «{item.label}» в ({spot[0]:.1f}, {spot[1]:.1f})")

    def remove(self, op: RemoveOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        del self.objects[obj.id]
        self.placer.release(obj.id)
        self.applied.append(f"удалён {obj.id}")

    def move(self, op: MoveOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        # Сам объект не должен мешать себе на новом месте.
        self.placer.release(obj.id)
        spot = self._spot(op.x, op.z, self._setback_kind(obj), f"move {obj.id}", obj.id)
        if spot is None:
            self.placer.occupy(obj.id, obj.position.x, obj.position.z, obj.type)
            return
        x, z = spot
        self.objects[obj.id] = obj.model_copy(update={"position": Point3(x=x, y=obj.position.y, z=z)})
        self.placer.occupy(obj.id, x, z, obj.type)
        self.applied.append(f"перемещён {obj.id} в ({x:.1f}, {z:.1f})")

    def rotate(self, op: RotateOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        self.objects[obj.id] = obj.model_copy(update={"rotation": math.radians(op.rotation_deg)})
        self.applied.append(f"повёрнут {obj.id} на {op.rotation_deg:.0f}°")

    # --- Групповые операции ------------------------------------------------

    def place_along(self, op: PlaceAlongOp) -> None:
        what = f"вдоль: {TARGET_LABELS[op.target]}"
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
        spacing = _spacing_for(op.spacing_m, items)
        offset = self.placer.min_offset(op.target, kind, max(_half_depth(item) for item in items))
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
                if placed.has_within(x, z, 0.9 * spacing) or not self.placer.is_free(x, z, kind):
                    continue
                if half_length and not self._ends_fit(x, z, rotation, half_length, kind):
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

    def _ends_fit(self, x: float, z: float, rotation_deg: float, half_length: float, kind: Optional[str]) -> bool:
        # Направление локальной оси X при повороте θ -- (cosθ, -sinθ), см.
        # Placer.points_along.
        angle = math.radians(rotation_deg)
        dx, dz = math.cos(angle) * half_length, -math.sin(angle) * half_length
        return self.placer.in_region(x + dx, z + dz, kind) and self.placer.in_region(x - dx, z - dz, kind)

    def _free_area(self, area_id: str):
        areas = self.placer.free_areas()
        label = area_id.strip().upper()
        if label.startswith("A") and label[1:].isdigit() and 1 <= int(label[1:]) <= len(areas):
            return areas[int(label[1:]) - 1]
        return None

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
        spacing = _spacing_for(op.spacing_m, items)

        area = None
        where = "по всему участку"
        if op.area is not None:
            area = self._free_area(op.area)
            if area is None:
                self.rejected.append(f"{what}: нет свободной области {op.area!r}")
                return
            where = f"по области {op.area}"

        if op.x is None or op.z is None:
            candidates = self.placer.points_in_area(kind, spacing / 2, area, count * CANDIDATES_PER_PLACEMENT)
            free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind)]
            chosen = pick_spread(free, count, 0.9 * spacing)
        else:
            center = (op.x, op.z)
            radius = clamp(op.radius_m or max(8.0, 1.2 * spacing * math.sqrt(count)), 1.0, MAX_AREA_RADIUS_M)
            # "У входа" в радиусе 5 м для дерева пусто по определению (5 м от
            # стены), поэтому при нехватке места круг один раз расширяем.
            for attempt in (radius, radius + MAX_SNAP_DISTANCE_M):
                circle = Point(center).buffer(attempt)
                within = circle if area is None else area.intersection(circle)
                candidates = self.placer.points_in_area(kind, spacing / 4, within, CANDIDATES_NEAR_POINT)
                free = [p for p in candidates if self.placer.is_free(p[0], p[1], kind)]
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

    def _resolve_endpoint(self, obj_id: Optional[str], target: Optional[str], x: Optional[float], z: Optional[float], label: str):
        """Точка или зона-цель для одного конца connect. Ровно один способ
        задания должен сработать: по id объекта, по имени цели или явными
        координатами."""
        if obj_id is not None:
            obj = self.objects.get(obj_id)
            if obj is None:
                self.rejected.append(f"connect: {label} — объекта {obj_id!r} нет")
                return None
            return Point(obj.position.x, obj.position.z)
        if target is not None:
            geom = self.placer.target_geometry(target)
            if geom is None:
                self.rejected.append(f"connect: {label} — на участке нет цели «{TARGET_LABELS[target]}»")
                return None
            return geom
        if x is not None and z is not None:
            return Point(x, z)
        self.rejected.append(f"connect: не указана точка «{label}» (id, target или x/z)")
        return None

    def connect(self, op: ConnectOp) -> None:
        """Дорожка между двумя точками/зонами: design_area строит целую сеть
        и уже сама подводит её к подъездам и к парковке, но не умеет вести
        дорожку к произвольному объекту (например к фонтану, добавленному
        отдельно) или дотягивать существующую сеть до цели по отдельной
        просьбе -- для этого и нужна эта операция."""
        a = self._resolve_endpoint(op.from_id, op.from_target, op.from_x, op.from_z, "начало")
        b = self._resolve_endpoint(op.to_id, op.to_target, op.to_x, op.to_z, "конец")
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
        link = LineString([(near_a.x, near_a.y), (near_b.x, near_b.y)])
        if link.length < 1.0:
            self.rejected.append("connect: точки и так рядом — соединять нечего")
            return
        clipped = link.intersection(area)
        pieces = [clipped] if clipped.geom_type == "LineString" else [g for g in getattr(clipped, "geoms", []) if g.geom_type == "LineString"]
        covered = sum(p.length for p in pieces)
        if covered < link.length - 1.0:
            self.rejected.append("connect: прямая дорожка перекрыта зданием или дорогой — соединение недоступно")
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
        self.applied.append(f"проложена дорожка: {_plural(placed, _OBJECT_FORMS)}, {covered:.0f} м")

    def remove_where(self, op: RemoveWhereOp) -> None:
        types = list(dict.fromkeys(op.object_types))
        locked = [t for t in types if t not in self.editable]
        types = [t for t in types if t in self.editable]
        if locked:
            target = self.warnings if types else self.rejected
            target.append(f"удаление: объекты «{', '.join(locked)}» менять нельзя")
        if not types:
            return

        conditions = []
        distance = None
        if op.target is not None:
            if self.placer.target_geometry(op.target) is None:
                self.rejected.append(f"удаление: на участке нет цели «{TARGET_LABELS[op.target]}»")
                return
            distance = clamp(
                op.distance_m if op.distance_m is not None else DEFAULT_REMOVE_DISTANCE_M, 0.0, MAX_AREA_RADIUS_M
            )
            conditions.append(f"ближе {distance:.1f} м: {TARGET_LABELS[op.target]}")
        circle = None
        if op.x is not None and op.z is not None:
            circle = (op.x, op.z, clamp(op.radius_m or DEFAULT_REMOVE_RADIUS_M, 0.5, MAX_AREA_RADIUS_M))
            conditions.append(f"в радиусе {circle[2]:.0f} м от ({op.x:.1f}, {op.z:.1f})")

        removed = 0
        for obj in list(self.objects.values()):
            if obj.type not in types:
                continue
            x, z = obj.position.x, obj.position.z
            if circle is not None and math.hypot(x - circle[0], z - circle[1]) > circle[2]:
                continue
            if op.target is not None and self.placer.target_distance(op.target, x, z) > distance:
                continue
            del self.objects[obj.id]
            self.placer.release(obj.id)
            removed += 1

        scope = f" ({'; '.join(conditions)})" if conditions else ""
        if removed == 0:
            self.rejected.append(f"удаление {', '.join(types)}{scope}: подходящих объектов нет")
            return
        self.applied.append(f"удалено {_plural(removed, _OBJECT_FORMS)} ({', '.join(types)}){scope}")

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


def apply_plan(
    scene: Scene,
    plan: LlmPlan,
    catalog: list[CatalogItem],
    placer: Optional[Placer] = None,
) -> TextEditResult:
    return _PlanApplier(scene, catalog, placer or Placer(scene)).run(plan)


def edit_scene_with_text(scene: Scene, instruction: str) -> TextEditResult:
    catalog = load_catalog()
    # Один планировщик на запрос: допустимые области, построенные для
    # контекста модели, переиспользуются при применении плана.
    placer = Placer(scene)
    plan = request_plan(scene, instruction, catalog, placer)
    logger.info("план: %s", json.dumps(plan.operations, ensure_ascii=False)[:1500])

    started = time.monotonic()
    result = apply_plan(scene, plan, catalog, placer)
    logger.info(
        "операций %d за %.2f с: применено %d, отклонено %d, предупреждений %d",
        len(plan.operations),
        time.monotonic() - started,
        len(result.applied),
        len(result.rejected),
        len(result.warnings),
    )
    for line in result.applied:
        logger.info("  применено: %s", line)
    for reason in result.rejected:
        logger.info("  отклонено: %s", reason)
    return result
