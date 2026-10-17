"""
Подписи подземных сетей: диаметр трубы из подписи («d=200ст.», «d=2х150»,
«d=2x250 1720х1100») и выноски, на полках которых подписи стоят.

СП 42.13330.2016, прим. 5 к табл. 9.1: расстояние от дерева до сети -- между
наружными поверхностями ствола и трубы. Зона сети с известным диаметром --
сама труба (коридор шириной в наружный диаметр), и норма отступа от её края
применяется ровно как в СП. Сеть без подписи диаметра -- прежний коридор
шириной 2 x minDistance (с запасом: диаметр неизвестен).

Выноска -- ломаная из двух-трёх точек на слое сети: кончик у трубы, полка,
на полке подпись. Это не трасса: без распознавания выноска длиной до 30 м
становилась коридором «газопровода» поперёк газона. Признак выноски (замер
на тайлах up датасета): подпись лежит на последнем отрезке, и длина этого
отрезка -- ширина подписи (0,8..1,0 от числа знаков x высота x 0,8).

Пунктир трассы нарисован отдельными штрихами с промежутками 0,4..0,6 м;
штрихи, продолжающие друг друга по прямой, собираются в одну трассу
(trace_runs), и подпись диаметра относится ко всей трассе до развилки.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

# Подписи: «d=200», «d = 2х150», «d=2x250 1720х1100» (сечение канала
# теплосети), «Ø300». Буква «х» -- и латинская, и русская.
_DIAMETER = re.compile(r"(?:\bd|ø|Ø|⌀)\s*=?\s*(?:(\d)\s*[xх×]\s*)?(\d{2,4})(?!\d)", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<!\d)(\d{3,4})\s*[xх×]\s*(\d{3,4})(?!\d)")
# Правдоподобный наружный размер, м: от газовой трубы 15 мм до коллектора.
_MIN_WIDTH_M, _MAX_WIDTH_M = 0.015, 4.0

# Выноска: подпись не дальше этой доли высоты текста от полки; длина полки
# -- от и до этих долей ширины подписи; вся выноска не длиннее.
_LEADER_TEXT_GAP = 0.8
_SHELF_TO_TEXT_WIDTH = (0.5, 1.6)
_TEXT_WIDTH_PER_CHAR = 0.8
_MAX_LEADER_M = 50.0

# Штрихи одной трассы: промежуток пунктира 0,4..0,6 м, с запасом.
RUN_BRIDGE_M = 0.8
# Подпись без выноски относится к трассе не дальше стольких высот текста.
_LABEL_REACH_HEIGHTS = 2.0
# Кончик выноски -- у трубы: не дальше этого от трассы, м.
_TIP_REACH_M = 0.5

NETWORK_TYPES = ("gas_pipeline", "sewer", "water_pipeline", "heat_network")


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    height: float


@dataclass(frozen=True)
class Annotation:
    """Подпись сети с местом, к которому она относится: кончик выноски или
    сама подпись (подписи вдоль трубы без выноски)."""

    text: str
    x: float
    y: float
    on_leader: bool
    height: float


def outer_width_m(text: str) -> Optional[float]:
    """Наружная ширина сети по подписи, м, или None, если в подписи нет
    диаметра. Сечение канала («1720х1100») главнее диаметра труб в нём;
    несколько труб («d=2х150») -- суммарная ширина."""
    channel = _CHANNEL.search(text.replace(",", "."))
    diameter = _DIAMETER.search(text)
    width = None
    if channel and not (diameter and channel.start() <= diameter.end() and diameter.group(1)):
        width = int(channel.group(1)) / 1000
    elif diameter:
        count = int(diameter.group(1) or 1)
        width = count * int(diameter.group(2)) / 1000
    if width is None or not _MIN_WIDTH_M <= width <= _MAX_WIDTH_M:
        return None
    return width


def label_of(entity) -> Optional[Label]:
    """Подпись из TEXT или MTEXT (без форматирования MTEXT)."""
    kind = entity.dxftype()
    try:
        if kind == "TEXT":
            text, height = entity.dxf.text, entity.dxf.height
        elif kind == "MTEXT":
            text, height = entity.plain_text(), entity.dxf.char_height
        else:
            return None
        insert = entity.dxf.insert
    except Exception:  # noqa: BLE001 -- битая подпись -- не подпись
        return None
    text = (text or "").strip()
    if not text or height <= 0:
        return None
    return Label(text, insert.x, insert.y, height)


class LabelIndex:
    """Подписи одного слоя с пространственным индексом."""

    def __init__(self, labels: list[Label]):
        self.labels = labels
        self._points = [Point(lb.x, lb.y) for lb in labels]
        self._tree = STRtree(self._points) if labels else None

    def near(self, geometry, distance: float) -> list[int]:
        if self._tree is None:
            return []
        return [int(i) for i in self._tree.query(geometry, predicate="dwithin", distance=distance)]

    def point(self, index: int) -> Point:
        return self._points[index]


def leader_annotation(pts: list[tuple[float, float]], labels: LabelIndex, scale: float) -> Optional[tuple[int, Annotation]]:
    """(индекс подписи, подпись на кончике выноски), если ломаная pts --
    выноска; иначе None. scale -- метров в единице чертежа."""
    if len(pts) not in (2, 3) or labels._tree is None:
        return None
    if LineString(pts).length * scale > _MAX_LEADER_M:
        return None
    # Полка -- последний отрезок с любого конца.
    for tip, shelf_pts in ((pts[0], pts[-2:]), (pts[-1], pts[:2])):
        shelf = LineString(shelf_pts)
        if shelf.length <= 0:
            continue
        for i in labels.near(shelf, max(lb.height for lb in labels.labels) * _LEADER_TEXT_GAP):
            label = labels.labels[i]
            where = labels.point(i)
            if shelf.distance(where) > label.height * _LEADER_TEXT_GAP or where.distance(Point(tip)) <= label.height * 0.5:
                continue
            width = len(label.text) * label.height * _TEXT_WIDTH_PER_CHAR
            low, high = _SHELF_TO_TEXT_WIDTH
            if low * width <= shelf.length <= high * width:
                return i, Annotation(label.text, tip[0], tip[1], True, label.height)
    return None


# Штрих продолжает штрих, если направления совпадают не хуже этого.
_CONTINUATION_COS = 0.9


def _ends(coords: list[tuple[float, float]]):
    """Два конца ломаной: (точка, направление наружу)."""
    (x0, y0), (x1, y1) = coords[0][:2], coords[1][:2]
    (xn, yn), (xm, ym) = coords[-1][:2], coords[-2][:2]
    return ((x0, y0), (x0 - x1, y0 - y1)), ((xn, yn), (xn - xm, yn - ym))


def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 0 else (0.0, 0.0)


def trace_runs(lines: list[list[tuple[float, float]]], bridge: float) -> list[int]:
    """Номер трассы для каждой линии. Штрихи пунктира сшиваются в трассу,
    если конец одного продолжает другой по прямой (взаимно лучшая пара в
    пределах bridge, единицы чертежа); на развилке трасса рвётся -- ветка
    другого диаметра не получает диаметр магистрали."""
    if not lines:
        return []
    ends = []  # (номер линии, точка, единичное направление наружу)
    for index, coords in enumerate(lines):
        for point, direction in _ends(coords):
            ends.append((index, point, _unit(direction)))
    tree = STRtree([Point(p) for _i, p, _d in ends])

    def best(k):
        line, point, out = ends[k]
        choice, gap_best = None, math.inf
        for j in tree.query(Point(point), predicate="dwithin", distance=bridge).tolist():
            other, q, other_out = ends[j]
            if other == line:
                continue
            # Продолжение: выходит навстречу, и промежуток -- по ходу трассы.
            if -(out[0] * other_out[0] + out[1] * other_out[1]) < _CONTINUATION_COS:
                continue
            gap = math.dist(point, q)
            if gap > 1e-9:
                along = ((q[0] - point[0]) * out[0] + (q[1] - point[1]) * out[1]) / gap
                if along < _CONTINUATION_COS:
                    continue
            if gap < gap_best:
                choice, gap_best = j, gap
        return choice

    partner = [best(k) for k in range(len(ends))]
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for k, j in enumerate(partner):
        if j is not None and partner[j] == k:
            a, b = find(ends[k][0]), find(ends[j][0])
            if a != b:
                parent[a] = b
    return [find(i) for i in range(len(lines))]


def run_widths(
    lines: list[list[tuple[float, float]]], annotations: list[Annotation], scale: float
) -> tuple[list[int], dict[int, tuple[float, str]]]:
    """(номер трассы каждой линии, {номер трассы: (наружная ширина в
    единицах чертежа, подпись)}) -- для трасс, к которым относится подпись с
    диаметром. Если подписей у трассы несколько -- берётся самая широкая (с
    запасом)."""
    runs = trace_runs(lines, RUN_BRIDGE_M / scale)
    if not runs or not annotations:
        return runs, {}
    geoms = [LineString(c) for c in lines]
    tree = STRtree(geoms)
    found: dict[int, tuple[float, str]] = {}
    for note in annotations:
        width = outer_width_m(note.text)
        if width is None:
            continue
        reach = _TIP_REACH_M / scale if note.on_leader else note.height * _LABEL_REACH_HEIGHTS
        where = Point(note.x, note.y)
        near = [int(i) for i in tree.query(where, predicate="dwithin", distance=reach)]
        if not near:
            continue
        closest = min(near, key=lambda i: geoms[i].distance(where))
        run = runs[closest]
        width_units = width / scale
        if run not in found or found[run][0] < width_units:
            found[run] = (width_units, note.text)
    return runs, found


# Ветка выноски у кончика идёт к трубе под углом; штрих пунктира лежит
# на одной прямой с соседними.
_MIN_LEADER_ANGLE_DEG = 15.0


def _direction_at(line: LineString, point: Point) -> tuple[float, float]:
    d = line.project(point)
    eps = min(0.05 * line.length, 0.5)
    a = line.interpolate(max(0.0, d - eps))
    b = line.interpolate(min(line.length, d + eps))
    return b.x - a.x, b.y - a.y


def _angle_deg(u: tuple[float, float], v: tuple[float, float]) -> float:
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu == 0 or nv == 0:
        return 0.0
    cos = abs(u[0] * v[0] + u[1] * v[1]) / (nu * nv)
    return math.degrees(math.acos(min(1.0, cos)))


def split_leaders(
    lines: list[list[tuple[float, float]]], labels: LabelIndex, scale: float
) -> tuple[list[list[tuple[float, float]]], list[Annotation]]:
    """(линии трасс без выносок, подписи слоя). Выноска -- см. докстринг
    модуля, плюс проверка по соседям: кончик касается трассы под углом, а
    дальний конец полки свободен. Подписи, не стоящие на выносках, --
    подписи вдоль трубы (on_leader=False)."""
    geoms = [LineString(c) for c in lines]
    tree = STRtree(geoms) if geoms else None
    bridge = RUN_BRIDGE_M / scale
    kept, notes, used = [], [], set()

    def others_near(point: Point, own: int) -> list[int]:
        return [int(i) for i in tree.query(point, predicate="dwithin", distance=bridge) if int(i) != own]

    for index, coords in enumerate(lines):
        found = leader_annotation(coords, labels, scale) if tree is not None else None
        if found is not None:
            label_index, note = found
            tip = Point(note.x, note.y)
            far = Point(coords[-1]) if (coords[0][0], coords[0][1]) == (note.x, note.y) else Point(coords[0])
            touching = others_near(tip, index)
            leg_end = coords[1] if (coords[0][0], coords[0][1]) == (note.x, note.y) else coords[-2]
            leg = (leg_end[0] - note.x, leg_end[1] - note.y)
            angled = any(_angle_deg(leg, _direction_at(geoms[i], tip)) >= _MIN_LEADER_ANGLE_DEG for i in touching)
            if touching and angled and not others_near(far, index):
                notes.append(note)
                used.add(label_index)
                continue
        kept.append(coords)
    for i, label in enumerate(labels.labels):
        if i not in used:
            notes.append(Annotation(label.text, label.x, label.y, False, label.height))
    return kept, notes


def diameter_mm(text: str) -> Optional[int]:
    """Диаметр из подписи, мм -- для сообщения зоны."""
    m = _DIAMETER.search(text)
    return int(m.group(2)) if m else None
