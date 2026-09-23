"""
Контекст и системный промпт для LLM правки текстом (text_editor/service.py): что
модель видит об участке -- контур, здания, ориентиры, цели для рядов,
свободные области, каталог. Всё в сжатом виде: каждый символ контекста --
токены и деньги на каждом запросе (см. лимиты ниже).
"""

from __future__ import annotations

import json
import math
from collections import Counter

from shapely.geometry import Polygon

from core.placement import (
    TARGET_LABELS,
    Placer,
)
from core.plant_catalog import CATALOG as BASE_CATALOG
from core.plant_catalog import CatalogItem
from core.schemas import Scene

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

    # Зоны, которые пользователь выделил вручную мышкой на плане (фронтенд
    # помечает их type="selection", severity "allowed" -- сам факт выделения
    # ничего не запрещает, см. Placer.region()). Фильтр именно по type, а не по
    # одной только severity "allowed": в реальных участках (locations/) уже
    # встречаются свои "allowed"-зоны из DXF (например GRASS) -- это разметка
    # исходного плана, а не то, что пользователь только что выделил в
    # редакторе, и путать их в контексте для модели нельзя. Без этого поля
    # модель не может понять, что такое "здесь"/"в выделении" в просьбе
    # пользователя -- имя зоны нигде не появлялось в разговоре.
    selected_areas = [
        {"name": zone.name, "outline": _outline([(p.x, p.z) for p in zone.polygon])}
        for zone in scene.restrictions
        if zone.type == "selection" and len(zone.polygon) >= 3
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
        "selected_areas": selected_areas,
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

Контекст (JSON): контур участка, здания, ориентиры (landmarks: подъезды, площадки), цели для рядов (targets), свободные для посадки области (free_areas), выделенные пользователем мышкой участки (selected_areas: {"name", "outline"}), текущие объекты (objects) и каталог (catalog_rows, колонки описаны в catalog_columns). Координаты в метрах, контуры — точки [x, z].

Верни ТОЛЬКО валидный JSON без markdown, строго такой формы:
{"operations": [...], "explanation": "..."}

Групповые операции — для рядов, аллей, изгородей, «вдоль», «вокруг», «по периметру», «засадить», «много»:
- {"op": "place_along", "target": "<target из targets>", "catalog_ids": ["..."], "spacing_m": <необязательно>, "offset_m": <необязательно>, "max_count": <необязательно>, "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Ряд посадок вдоль контура цели с обеих сторон: pedestrian_path — вдоль дорожек, road — вдоль дорог, building — вдоль фасадов, parking — вокруг парковок, playground — вокруг детских площадок, site_boundary — по периметру участка. x, z, radius_m — только если ряд нужен в одном месте (например, у конкретного подъезда).
- {"op": "place_in_area", "catalog_ids": ["..."], "count": <сколько, необязательно, по умолчанию 5>, "spacing_m": <необязательно>, "area": "<id из free_areas, необязательно>", "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Группа посадок: с x и z — компактно вокруг точки; с area — равномерно по свободной области; без них — равномерно по всему участку. count указывай по числу из просьбы, но если просьба только про разнообразие видов без числа ("посади разные виды деревьев") — не выдумывай число сам, просто не указывай count вовсе.
- {"op": "remove_where", "object_types": ["<тип из objects>"], "target": "<необязательно>", "distance_m": <необязательно>, "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Удалить все объекты этих типов, подходящие под фильтры: у цели ближе distance_m (по умолчанию 3 м) и/или в радиусе от точки. Без фильтров — все объекты этих типов. object_types можно не указывать («очисти эту зону») — тогда под фильтры проверяются все виды объектов, какие есть.
- {"op": "define_zone", "name": "<имя>", "severity": "forbidden"/"warning"/"allowed", "message": <необязательно>, "x"+"z"/"around_id"/"around_target", "radius_m": <необязательно>}
  Выделить именованную зону («детская зона», «здесь ничего не сажать», «зона под цветник») — круг вокруг точки, объекта или цели. Зона сразу видна на плане и учитывается всеми правками (severity "forbidden"/"warning" — туда ничего не сажать; "allowed" — просто пометить). После этого её можно называть по имени в target любой операции (place_along, remove_where, ...) и в area у place_in_area/cover_area — так и решается «посади цветы в этой зоне».
- {"op": "design_area", "elements": ["paths", "flowerbeds", "fountain", "lamps", "benches", "trash", "hedge", "trees", "bushes"], "style": "<необязательно>", "tree_ids": [<необязательно>], "bush_ids": [<необязательно>], "x": <необязательно>, "z": <необязательно>, "radius_m": <необязательно>}
  Полный дизайн двора одной операцией: планировщик сам прокладывает каркас дорожек (от подъезда к подъезду, с выходом на парковку, если она рядом), расставляет фонари и скамейки с урнами вдоль дорожек, живую изгородь по краю двора, деревья вразброс по свободной площади. В elements перечисли то, что просили: paths — дорожки (прокладываются всегда), flowerbeds — клумбы, fountain — фонтан, lamps — фонари, benches — скамейки, trash — урны, hedge — живая изгородь, trees — деревья, bushes — кусты. Если просят «дизайн», «благоустройство», «сквер», «парк» без перечня — elements не указывай (по умолчанию — всё, КРОМЕ фонтана). fountain указывай, только если фонтан просят явно: площадь для него есть не в каждом дворе, и без явной просьбы он не ставится. style — шаблон каркаса дорожек: spine (дорожки от подъезда к подъезду — по умолчанию для обычного двора), diagonal (площадь на пересечении диагоналей — только если явно просят «крест», «по диагонали»), grid (сетка дорожек, для большого двора), perimeter (дорожка по периметру, для узкого двора); без явной просьбы про форму дорожек не указывай — планировщик сам подберёт по форме двора. x, z, radius_m — только если дизайн нужен в конкретной части участка.
- {"op": "connect", "from_id"/"from_target"/"from_x"+"from_z": "<одно из трёх>", "to_id"/"to_target"/"to_x"+"to_z": "<одно из трёх>"}
  Проложить дорожку между двумя точками: подъезд-подъезд, подъезд или другой объект (например фонтан) — id из objects; сеть дорожек или граница участка до зоны (например парковки) — target из targets. Для "от подъезда к Х" или "соедини сеть дорожек с Y" — эта операция, а не place_along. design_area уже сама тянет дорожки к подъездам и парковке — connect нужен для точечной, дополнительной связи. Если прямая между точками перекрыта зданием, планировщик сам обходит его по кратчайшему пути — не отказывай заранее из-за препятствия, пробуй connect.
- {"op": "cover_area", "catalog_ids": ["<газон/цветник>"], "area"/"x"+"z"+"radius_m": <необязательно>}
  Сплошной ковёр травяного покрытия или цветника (виды с category "groundcover") по области — плитки укладываются почти встык, а не редкой россыпью, как place_in_area. Без area/x,z — по всему участку.
- {"op": "line_of", "catalog_ids": ["..."], "from_id"/"from_target"/"from_x"+"from_z", "to_id"/"to_target"/"to_x"+"to_z", "spacing_m": <необязательно>}
  Ряд объектов (изгородь, забор из фонарей) прямой линией между двумя произвольными точками/объектами/целями — не вдоль контура существующей цели (для этого place_along), а от точки А до точки Б.
- {"op": "enclose", "catalog_ids": ["..."], "around_id"/"around_target"/"around_x"+"around_z", "radius_m": <для around_id/around_x,z>, "offset_m": <необязательно>, "spacing_m": <необязательно>}
  Кольцо объектов (изгородь, забор, фонари) вокруг существующего объекта, цели (например playground) или точки — "огороди площадку", "обведи фонтан клумбами".
- {"op": "replace_where", "object_types": ["<тип>"], "catalog_ids": ["<новый вид>"], "target"/"distance_m"/"x"+"z"+"radius_m": <необязательно>}
  Заменить вид у существующих подходящих объектов (положение и поворот сохраняются) — "замени низкие деревья на высокие", "сделай кусты разнообразнее" (несколько catalog_ids вперемешку).
- {"op": "thin_out", "object_types": ["<тип>"], "min_spacing_m": <необязательно>, "target"/"distance_m"/"x"+"z"+"radius_m": <необязательно>}
  Убрать лишние объекты этих типов там, где они стоят гуще min_spacing_m, — "проредить кусты", "не так часто".
- {"op": "resize", "object_types": ["<тип>"], "scale": <множитель, 1.0 = как в каталоге>, "target"/"distance_m"/"x"+"z"+"radius_m": <необязательно>}
  Изменить масштаб существующих объектов — "сделай деревья у входа покрупнее" (scale > 1) / помельче (scale < 1).
- {"op": "face", "object_types": ["<тип>"], "at_id"/"at_target"/"at_x"+"at_z", "target"/"distance_m"/"x"+"z"+"radius_m": <необязательно, какие именно объекты>}
  Развернуть существующие объекты к точке/объекту/цели — "разверни лавки к фонтану", "разверни фонари к дорожке".
- {"op": "align_along", "object_types": ["<тип>"], "target": "<target>", "spacing_m"/"offset_m": <необязательно>, "x"+"z"+"radius_m": <необязательно>}
  Подровнять уже стоящие вразнобой объекты в аккуратный ряд вдоль цели — передвигает существующие, не добавляет новые. Для "выровняй фонари вдоль дорожки", когда они и так там стоят, но криво.
- {"op": "duplicate_near", "id": "<id объекта>", "near_id"/"near_target"/"near_x"+"near_z", "count": <необязательно, по умолчанию 1>}
  Скопировать существующий объект (тот же вид) рядом с другой точкой/объектом/целью — "сделай такую же лавку у второго подъезда".
- {"op": "set_count", "object_types": ["<тип>"], "count": <нужное число>, "catalog_ids": [<необязательно, чем добавлять, если не хватает>], "target"/"distance_m"/"x"+"z"+"radius_m": <необязательно>}
  Довести суммарное число объектов этих типов (по всему участку/у цели/в области) ровно до count — добавит недостающие или уберёт лишние.

Точечные операции — только для конкретных объектов или одной-двух посадок в названном месте:
- {"op": "add", "catalog_id": "...", "x": <число>, "z": <число>, "rotation_deg": <необязательно>}
- {"op": "remove", "id": "<id из objects>"}
- {"op": "move", "id": "<id из objects>", "x": <число>, "z": <число>}
- {"op": "rotate", "id": "<id из objects>", "rotation_deg": <число>}

Правила:
- catalog_id бери только из catalog_rows, id — только из objects, target — только из targets ИЛИ имени зоны, выделенной define_zone, area — только из free_areas ИЛИ имени такой зоны. Не выдумывай.
- object_types (remove_where/replace_where/thin_out/resize/face/set_count) можно не указывать — тогда под фильтры проверяются все виды объектов сразу; align_along всегда требует конкретный тип (ряд из разнородных объектов не построить).
- Деревья, кустарники, МАФ и покрытия — разными операциями. Кустарники — строки с category "bush" (живая изгородь — только hedge_segment), деревья — "tree", газон/цветник для cover_area — "groundcover".
- В catalog_ids — один или несколько видов подходящего класса; одинаковые деревья сажать можно.
- spacing_m и offset_m не указывай, если пользователь не просит гуще, реже или дальше: шаг по размеру вида планировщик возьмёт сам.
- count и max_count — по числу из просьбы; «несколько» — 3–5. Для «вдоль», «по периметру», «засади» без числа max_count не указывай.
- Если в просьбе вместе дорожки, скамейки, урны, фонари, клумбы, изгородь или «благоустрой/спроектируй двор», «сделай сквер/парк» — ОДНА операция design_area, а не отдельные посадки.
- «У входа» — координаты подъезда из landmarks. «В центре двора» — center самой большой области из free_areas. «Вокруг фонтана» / «у скамейки» и т.п. — x, z существующего объекта нужного типа из objects (не landmark и не target). «Здесь» / «в этой области» / «в выделении» / просьба без явного места, когда selected_areas не пуст, — это выделенный пользователем участок: используй его name как target (place_along/remove_where/...) или area (place_in_area/cover_area).
- Здания, подъезды, дорожки и зоны менять нельзя.
- Если просьба невыполнима (например, нужной цели нет в targets) — пустой operations и причина в explanation.
- explanation — одно-два предложения по-русски: что сделано. Точное количество не называй: его посчитает планировщик.

Пример 1. Просьба: «посади кусты вдоль дорожек и два дерева у первого подъезда».
{"operations": [{"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium", "bush_tall", "bush_short"]}, {"op": "place_in_area", "catalog_ids": ["<catalog_id дерева из catalog_rows>"], "count": 2, "x": 12.5, "z": -30.0, "radius_m": 10}], "explanation": "Вдоль дорожек высажены кустарники, у первого подъезда — два дерева."}

Пример 2. Просьба: «добавь деревья вокруг фонтана», в objects есть {"id": "fountain_llm_a1b2c3d4", "type": "fountain", "x": 5.0, "z": -12.0, ...}.
{"operations": [{"op": "place_in_area", "catalog_ids": ["<catalog_id дерева>"], "count": 4, "x": 5.0, "z": -12.0, "radius_m": 8}], "explanation": "Вокруг фонтана посажены четыре дерева."}

Пример 3. Просьба: «выдели зону под детскую площадку у второго подъезда радиусом 10 м и посади там кусты по кругу».
{"operations": [{"op": "define_zone", "name": "Детская площадка", "severity": "forbidden", "x": 12.5, "z": -30.0, "radius_m": 10}, {"op": "enclose", "catalog_ids": ["<catalog_id куста>"], "around_target": "Детская площадка", "offset_m": 1}], "explanation": "Выделена зона под детскую площадку, по её краю высажены кусты."}"""
