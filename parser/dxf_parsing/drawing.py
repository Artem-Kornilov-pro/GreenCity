"""
Что разбор видит в чертеже: сущности modelspace с раскрытыми вставками блоков
и ключ каждой сущности для правил слоёв (layer_names.LayerResolver).

Подоснову и проект часто вставляют в чертёж блоком: весь топоплан -- одна
вставка на слое «0», здания -- блоки на слое «Здания». Такая вставка
раскрывается в сущности блока так, как её рисует CAD: координаты переводятся
в чертёж, сущность со слоя «0» берёт слой вставки. Вставка-условный знак
(дерево, фонарь, скамейка) остаётся точкой, как и раньше.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Optional

from ezdxf.query import EntityQuery

from dxf_parsing.encoding import repair_mojibake
from dxf_parsing.layer_names import BOUNDARY_TIER_KEYS, LayerResolver, _point_rule, describe_key, is_known
from dxf_parsing.rules import POLYGON_RULES, match_rule

# Глубже вложенные блоки не раскрываются -- защита от рекурсии в битых файлах.
MAX_BLOCK_DEPTH = 8
# Сколько сущностей может дать раскрытие блоков: дальше вставки остаются как
# есть, а не занимают память без предела.
MAX_EXPLODED_ENTITIES = 1_500_000
# Блок условного знака, в котором две и больше вставки-знака (точки, круги с
# разными центрами), -- группа посадок («аллея», «куртина», весь слой
# деревьев одним блоком), а не одно растение: раскрывается.
_GROUP_MIN_SYMBOLS = 2


def _kind_label(kind: Optional[str], label: str) -> str:
    """Подпись класса без названия вида: «Деревья», а не «Деревья: Липа…» --
    в сводке слоя важен класс."""
    return label.split(":", 1)[0] if kind in ("tree", "bush") else label


def _distinct_centers(circles) -> int:
    """Сколько разных мест у окружностей: концентрические круги (крона и
    ствол одного дерева) -- одно место."""
    centers: list = []
    for c in circles:
        x, y, r = c.dxf.center.x, c.dxf.center.y, c.dxf.radius
        if all(math.hypot(x - cx, y - cy) > max(r, cr) for cx, cy, cr in centers):
            centers.append((x, y, r))
            if len(centers) >= _GROUP_MIN_SYMBOLS:
                break
    return len(centers)


class DrawingView:
    """Сущности чертежа для разбора. Поддерживает то, чем парсер пользуется у
    modelspace: перебор и query("LINE LWPOLYLINE")."""

    def __init__(self, layout, resolver: Optional[LayerResolver] = None):
        self.resolver = resolver or LayerResolver()
        self.entities: list = []
        self.exploded_inserts = 0
        self.used_paperspace = False
        self._group_cache: dict[str, bool] = {}
        self._flatten(layout, 0)

    @classmethod
    def of_document(cls, doc, resolver: Optional[LayerResolver] = None) -> DrawingView:
        """modelspace; если в нём ничего не распознано, а на листах есть
        чертёж (так бывает у DXF, выгруженных из листа), -- и листы."""
        view = cls(doc.modelspace(), resolver)
        if not view.has_recognized():
            for name in doc.layouts.names():
                if name == "Model":
                    continue
                before = len(view.entities)
                view._flatten(doc.layouts.get(name), 0)
                view.used_paperspace = view.used_paperspace or len(view.entities) > before
        return view

    def __iter__(self):
        return iter(self.entities)

    def __len__(self):
        return len(self.entities)

    def query(self, query: str = "*") -> EntityQuery:
        return EntityQuery(self.entities, query)

    def key(self, entity) -> str:
        return self.resolver.key(entity)

    def has_recognized(self) -> bool:
        return any(is_known(self.key(e)) for e in self.entities if e.dxftype() not in ("TEXT", "MTEXT"))

    # --- раскрытие вставок --------------------------------------------------

    def _flatten(self, entities, depth: int) -> None:
        for entity in entities:
            if entity.dxftype() == "INSERT" and depth < MAX_BLOCK_DEPTH and self._should_explode(entity):
                children = self._explode(entity)
                if children is not None:
                    self.exploded_inserts += 1
                    self._flatten(children, depth + 1)
                    continue
            self.entities.append(entity)

    def _is_symbol(self, insert) -> bool:
        return _point_rule(self.key(insert)) is not None

    def _should_explode(self, insert) -> bool:
        if len(self.entities) >= MAX_EXPLODED_ENTITIES:
            return False
        if not self._is_symbol(insert):
            return True
        return self._is_group(insert)

    def _is_group(self, insert) -> bool:
        """Блок знака на самом деле -- группа объектов или площадь, и его
        нужно раскрыть: в нём несколько вставок-знаков, несколько точек,
        несколько окружностей с разными центрами (посадки целым слоём в одном
        блоке) или, если ключ даёт и зону (площадка), -- контуры площади."""
        cache_key = (insert.dxf.name, self.key(insert))
        if cache_key not in self._group_cache:
            try:
                block = insert.block()
                self._group_cache[cache_key] = block is not None and self._block_is_group(block, cache_key[1])
            except Exception:  # noqa: BLE001 -- блок не читается: оставляем вставку знаком
                self._group_cache[cache_key] = False
        return self._group_cache[cache_key]

    def _block_is_group(self, block, key: str) -> bool:
        symbols = points = 0
        circles: list = []
        has_area = False
        for e in block:
            kind = e.dxftype()
            # Вставка со слоя «0» внутри знака берёт его слой -- это тоже знак.
            if kind == "INSERT" and (e.dxf.layer == "0" or self._is_symbol(e)):
                symbols += 1
            elif kind == "POINT":
                points += 1
            elif kind == "CIRCLE":
                circles.append(e)
            elif kind == "HATCH" or (kind == "LWPOLYLINE" and e.closed) or (kind == "POLYLINE" and e.is_closed):
                has_area = True
            if symbols >= _GROUP_MIN_SYMBOLS or points >= _GROUP_MIN_SYMBOLS:
                return True
        if _distinct_centers(circles) >= _GROUP_MIN_SYMBOLS:
            return True
        return has_area and match_rule(key, POLYGON_RULES) is not None

    def _explode(self, insert) -> Optional[list]:
        """Сущности блока в координатах чертежа или None, если блок не
        раскрывается (внешняя ссылка без файла, битый блок)."""
        try:
            block = insert.block()
            if block is None or block.block_record.is_xref:
                return None
            copies = list(insert.multi_insert()) if insert.mcount > 1 else [insert]
            children = [child for copy in copies for child in copy.virtual_entities()]
        except Exception:  # noqa: BLE001 -- ezdxf не раскрыл блок: он остаётся вставкой
            return None
        inherited = self._inherited_layer(insert)
        for child in children:
            if child.dxf.layer == "0":
                child.dxf.layer = inherited
        return children

    def _inherited_layer(self, insert) -> str:
        """Слой для сущностей блока со слоя «0». Как в CAD -- слой вставки; но
        если он ничего не говорит, а имя блока говорит («Здание_5» на слое
        «0»), -- имя блока."""
        layer = insert.dxf.layer
        key, _ = self.resolver.resolve_layer(layer)
        if key is None and self.resolver.block_key(insert.dxf.name) is not None:
            return insert.dxf.name
        return layer

    # --- сводка -------------------------------------------------------------

    def layer_report(self, dropped: Optional[dict[str, int]] = None) -> list[dict]:
        """По слою: сколько сущностей, что из него взято и как слой распознан
        (override, name, normalized, synonym, content или None). dropped --
        слои, которые отброшены до разбора (пачка DWG), с числом сущностей."""
        counts: Counter = Counter()
        # Что взято со слоя по содержимому (имя блока, штриховка, тип линии),
        # а не по имени слоя: {слой: {подпись класса: число сущностей}}.
        content: dict[str, Counter] = {}
        for entity in self.entities:
            if entity.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION"):
                continue
            layer = entity.dxf.layer
            counts[layer] += 1
            entity_key = self.key(entity)
            if entity_key != self.resolver.layer_key(layer) and is_known(entity_key):
                kind, label = describe_key(entity_key)
                if label:
                    content.setdefault(layer, Counter())[_kind_label(kind, label)] += 1
        for layer, count in (dropped or {}).items():
            if layer not in counts:
                counts[layer] = count

        report = []
        for layer, count in counts.most_common():
            key, source = self.resolver.resolve_layer(layer)
            kind, label = describe_key(key)
            by_content = [name for name, _n in content.get(layer, Counter()).most_common(3)]
            if label is None and by_content:
                label, source = ", ".join(by_content), "content"
                kind = None
            elif by_content and source != "override":
                label = f"{label}; по содержимому: {', '.join(by_content)}"
            item = {"name": layer, "entities": count, "kind": kind, "label": label, "source": source if label else None}
            readable = repair_mojibake(layer)
            if readable != layer:
                item["displayName"] = readable
            report.append(item)
        return report


class ParsedContentFilter:
    """Нужна ли сущность разбору -- для отбора до разбора (пачка DWG, где
    топосъёмка вокруг участка занимает гигабайты). Нужны: тексты (подписи
    зданий), сущности с распознанным ключом и вставки блоков, в которых
    найдётся что-то распознанное (раскроются при разборе)."""

    def __init__(self, resolver: LayerResolver):
        self.resolver = resolver
        self._blocks: dict[tuple[str, bool], bool] = {}
        self._useful: dict[str, bool] = {}

    def _is_useful(self, key: str) -> bool:
        """Ключ, с которого разбор что-то возьмёт: оформление (подписи,
        штамп) и «не учитывать» -- не берёт. Граница, узнанная по синонимам
        («Граница участка», «Граница заказа»), -- особый ключ яруса, которого
        нет среди ключевых слов правил, но разбор её берёт."""
        if key not in self._useful:
            known = is_known(key) or key in BOUNDARY_TIER_KEYS
            self._useful[key] = known and describe_key(key)[0] not in ("skip", "ignore")
        return self._useful[key]

    def __call__(self, entity) -> bool:
        kind = entity.dxftype()
        if kind in ("TEXT", "MTEXT"):
            return True
        if self._is_useful(self.resolver.key(entity)):
            return True
        if kind == "INSERT":
            layer_key = self.resolver.resolve_layer(entity.dxf.layer)[0]
            block_key = self.resolver.block_key(entity.dxf.name)
            zero_known = any(k is not None and self._is_useful(k) for k in (layer_key, block_key))
            return self._block_has_content(entity, zero_known, 0)
        return False

    def _block_has_content(self, insert, zero_layer_known: bool, depth: int) -> bool:
        """Есть ли в блоке распознанное. Сущность со слоя «0» берёт слой
        вставки: она распознана, если распознан он (zero_layer_known)."""
        name = insert.dxf.name
        cache_key = (name, zero_layer_known)
        if cache_key in self._blocks:
            return self._blocks[cache_key]
        self._blocks[cache_key] = False  # от рекурсии в битом файле
        found = False
        try:
            block = insert.block()
            if block is not None and not block.block_record.is_xref and depth < MAX_BLOCK_DEPTH:
                for e in block:
                    if e.dxf.layer == "0" and zero_layer_known and e.dxftype() not in ("TEXT", "MTEXT", "ATTDEF"):
                        found = True
                    elif e.dxftype() == "INSERT":
                        inner_known = zero_layer_known if e.dxf.layer == "0" else self.resolver.resolve_layer(e.dxf.layer)[0] is not None
                        found = self._is_useful(self.resolver.key(e)) or self._block_has_content(e, inner_known, depth + 1)
                    else:
                        found = e.dxftype() not in ("TEXT", "MTEXT") and self._is_useful(self.resolver.key(e))
                    if found:
                        break
        except Exception:  # noqa: BLE001 -- блок не читается: в нём ничего не найдено
            found = False
        self._blocks[cache_key] = found
        return found
