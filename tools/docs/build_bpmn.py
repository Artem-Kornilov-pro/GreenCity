"""
Схема процесса GreenCity в нотации BPMN 2.0: от входного DXF до выходного DXF,
файла объяснений и пояснительной записки. Из одной раскладки строятся
docs/bpmn/greencity_process.bpmn (открывается в Camunda Modeler и на
demo.bpmn.io) и SVG для документации: общий процесс и подпроцесс GreenPlan.

    python3 tools/docs/build_bpmn.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs" / "bpmn"

ROW_H = 96
COL_W = 190
TASK_W, TASK_H = 168, 66
EVENT_D = 36
GATE_D = 46
STORE_W, STORE_H = 58, 48
OBJ_W, OBJ_H = 44, 56
LANE_HEAD = 34
MARGIN = 24
FONT = 12
CHAR_W = 6.6

FILL = {"task": "#ffffff", "llm": "#fff4d6", "sub": "#e3f3e6", "store": "#f2f2f2", "object": "#ffffff"}
STROKE = "#2b2b2b"


@dataclass
class Node:
    id: str
    kind: str  # start, end, task, llm, sub, xor, and, store, object
    name: str
    lane: str
    col: int
    row: int
    x: float = 0
    y: float = 0  # центр

    @property
    def size(self) -> tuple[float, float]:
        if self.kind in ("start", "end"):
            return EVENT_D, EVENT_D
        if self.kind in ("xor", "and"):
            return GATE_D, GATE_D
        if self.kind == "store":
            return STORE_W, STORE_H
        if self.kind == "object":
            return OBJ_W, OBJ_H
        return TASK_W, TASK_H

    @property
    def box(self) -> tuple[float, float, float, float]:
        w, h = self.size
        return self.x - w / 2, self.y - h / 2, w, h


@dataclass
class Diagram:
    id: str
    title: str
    lanes: list[tuple[str, str, int]]  # (id, подпись, число колонок)
    nodes: list[Node]
    flows: list[tuple[str, str, str]]  # (откуда, куда, подпись)
    associations: list[tuple[str, str]] = field(default_factory=list)  # (откуда, куда)
    pool: str | None = None
    rows: int = 0
    lane_x: dict[str, float] = field(default_factory=dict)
    width: float = 0
    height: float = 0

    def layout(self) -> None:
        self.rows = max(n.row for n in self.nodes) + 1
        top = MARGIN + (LANE_HEAD if self.pool else 0) + LANE_HEAD
        x = MARGIN
        for lane_id, _, cols in self.lanes:
            self.lane_x[lane_id] = x
            x += cols * COL_W
        self.width = x + MARGIN
        self.height = top + self.rows * ROW_H + MARGIN + 20
        for n in self.nodes:
            n.x = self.lane_x[n.lane] + n.col * COL_W + COL_W / 2
            n.y = top + n.row * ROW_H + ROW_H / 2

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)

    def route(self, src: Node, dst: Node) -> list[tuple[float, float]]:
        """Ортогональный маршрут сверху вниз: из развилки -- вбок, потом вниз;
        из прочих -- вниз, потом вбок, в боковую сторону цели."""
        sw, sh = src.size
        dw, dh = dst.size
        if abs(src.x - dst.x) < 1:
            return [(src.x, src.y + sh / 2), (dst.x, dst.y - dh / 2)]
        side = 1 if dst.x > src.x else -1
        if src.kind in ("xor", "and"):
            return [(src.x + side * sw / 2, src.y), (dst.x, src.y), (dst.x, dst.y - dh / 2)]
        return [(src.x, src.y + sh / 2), (src.x, dst.y), (dst.x - side * dw / 2, dst.y)]


def _wrap(text: str, width: float) -> list[str]:
    limit = max(8, int(width / CHAR_W))
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > limit:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line] if line else lines


def _text(x: float, y: float, lines: list[str], size: int = FONT, weight: str = "normal", anchor: str = "middle") -> str:
    first = y - (len(lines) - 1) * (size + 2) / 2
    spans = "".join(
        f'<tspan x="{x:.1f}" y="{first + i * (size + 2):.1f}">{escape(line)}</tspan>' for i, line in enumerate(lines)
    )
    return (
        f'<text font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}" dominant-baseline="middle" fill="{STROKE}">{spans}</text>'
    )


def _svg_node(n: Node) -> str:
    x, y, w, h = n.box
    parts = []
    if n.kind in ("start", "end"):
        sw = 1.5 if n.kind == "start" else 4
        parts.append(f'<circle cx="{n.x}" cy="{n.y}" r="{EVENT_D / 2}" fill="#fff" stroke="{STROKE}" stroke-width="{sw}"/>')
        lines = _wrap(n.name, COL_W * 0.95)
        dy = EVENT_D / 2 + 8 + len(lines) * 13 / 2
        parts.append(_text(n.x, n.y - dy if n.kind == "start" else n.y + dy, lines, size=11))
    elif n.kind in ("xor", "and"):
        d = GATE_D / 2
        parts.append(
            f'<polygon points="{n.x},{n.y - d} {n.x + d},{n.y} {n.x},{n.y + d} {n.x - d},{n.y}" '
            f'fill="#fff" stroke="{STROKE}" stroke-width="1.5"/>'
        )
        m = 9
        if n.kind == "xor":
            parts.append(
                f'<path d="M{n.x - m} {n.y - m} L{n.x + m} {n.y + m} M{n.x + m} {n.y - m} L{n.x - m} {n.y + m}" '
                f'stroke="{STROKE}" stroke-width="3"/>'
            )
        else:
            parts.append(
                f'<path d="M{n.x} {n.y - m - 2} L{n.x} {n.y + m + 2} M{n.x - m - 2} {n.y} L{n.x + m + 2} {n.y}" '
                f'stroke="{STROKE}" stroke-width="3"/>'
            )
        if n.name:
            parts.append(_text(n.x + d - 4, n.y - d - 6, _wrap(n.name, COL_W * 0.6), size=11, anchor="start"))
    elif n.kind == "store":
        e = 7
        parts.append(
            f'<path d="M{x} {y + e} A{w / 2} {e} 0 0 1 {x + w} {y + e} L{x + w} {y + h - e} '
            f'A{w / 2} {e} 0 0 1 {x} {y + h - e} Z" fill="{FILL["store"]}" stroke="{STROKE}" stroke-width="1.3"/>'
        )
        parts.append(f'<path d="M{x} {y + e} A{w / 2} {e} 0 0 0 {x + w} {y + e}" fill="none" stroke="{STROKE}" stroke-width="1.3"/>')
        parts.append(_text(n.x, y + h + 10 + 7, _wrap(n.name, COL_W * 0.95), size=11))
    elif n.kind == "object":
        f = 12
        parts.append(
            f'<path d="M{x} {y} L{x + w - f} {y} L{x + w} {y + f} L{x + w} {y + h} L{x} {y + h} Z" '
            f'fill="{FILL["object"]}" stroke="{STROKE}" stroke-width="1.3"/>'
        )
        parts.append(f'<path d="M{x + w - f} {y} L{x + w - f} {y + f} L{x + w} {y + f}" fill="none" stroke="{STROKE}" stroke-width="1.3"/>')
        parts.append(_text(n.x, y + h + 10 + 7, _wrap(n.name, COL_W * 0.95), size=11))
    else:
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{FILL[n.kind]}" '
            f'stroke="{STROKE}" stroke-width="{2 if n.kind == "sub" else 1.3}"/>'
        )
        lines = _wrap(n.name, w - 14)
        parts.append(_text(n.x, n.y - (6 if n.kind == "sub" else 0), lines, weight="bold" if n.kind == "sub" else "normal"))
        if n.kind == "sub":
            parts.append(f'<rect x="{n.x - 7}" y="{y + h - 16}" width="14" height="14" fill="#fff" stroke="{STROKE}"/>')
            parts.append(f'<path d="M{n.x - 4} {y + h - 9} L{n.x + 4} {y + h - 9} M{n.x} {y + h - 13} L{n.x} {y + h - 5}" stroke="{STROKE}" stroke-width="1.5"/>')
    return "".join(parts)


def render_svg(d: Diagram) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{d.width:.0f}" height="{d.height:.0f}" '
        f'viewBox="0 0 {d.width:.0f} {d.height:.0f}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="9" markerHeight="9" '
        f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{STROKE}"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    top = MARGIN
    body_bottom = d.height - MARGIN
    left, right = MARGIN, d.width - MARGIN
    if d.pool:
        parts.append(f'<rect x="{left}" y="{top}" width="{right - left}" height="{body_bottom - top}" fill="none" stroke="{STROKE}" stroke-width="1.5"/>')
        parts.append(_text((left + right) / 2, top + LANE_HEAD / 2, [d.pool], size=14, weight="bold"))
        parts.append(f'<line x1="{left}" y1="{top + LANE_HEAD}" x2="{right}" y2="{top + LANE_HEAD}" stroke="{STROKE}"/>')
        top += LANE_HEAD
    for lane_id, label, cols in d.lanes:
        lx = d.lane_x[lane_id]
        if d.pool or len(d.lanes) > 1:
            parts.append(f'<rect x="{lx}" y="{top}" width="{cols * COL_W}" height="{body_bottom - top}" fill="none" stroke="{STROKE}" stroke-width="1"/>')
            parts.append(f'<line x1="{lx}" y1="{top + LANE_HEAD}" x2="{lx + cols * COL_W}" y2="{top + LANE_HEAD}" stroke="{STROKE}" stroke-width="0.6"/>')
            parts.append(_text(lx + cols * COL_W / 2, top + LANE_HEAD / 2, [label], size=13, weight="bold"))
    for src_id, dst_id, label in d.flows:
        src, dst = d.node(src_id), d.node(dst_id)
        pts = d.route(src, dst)
        path = " ".join(f"{'M' if i == 0 else 'L'}{px:.1f} {py:.1f}" for i, (px, py) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{STROKE}" stroke-width="1.4" marker-end="url(#arrow)"/>')
        if label:
            (x1, y1), (x2, _) = pts[0], pts[1]
            if abs(x1 - x2) < 1:
                parts.append(_text(x1 + 6, y1 + 12, [label], size=11, anchor="start"))
            else:
                parts.append(_text(x1 + (14 if x2 > x1 else -14), y1 - 9, [label], size=11, anchor="start" if x2 > x1 else "end"))
    for src_id, dst_id in d.associations:
        a, b = d.node(src_id), d.node(dst_id)
        ax = a.x + (a.size[0] / 2 if b.x > a.x else -a.size[0] / 2)
        bx = b.x - (b.size[0] / 2 if b.x > a.x else -b.size[0] / 2)
        parts.append(
            f'<path d="M{ax:.1f} {a.y:.1f} L{bx:.1f} {b.y:.1f}" fill="none" stroke="{STROKE}" stroke-width="1.2" '
            'stroke-dasharray="4 4" marker-end="url(#arrow)"/>'
        )
    parts.extend(_svg_node(n) for n in d.nodes)
    parts.append("</svg>")
    return "\n".join(parts)


# --- BPMN XML ----------------------------------------------------------------

BPMN_TAG = {
    "start": "startEvent",
    "end": "endEvent",
    "task": "task",
    "llm": "serviceTask",
    "sub": "subProcess",
    "xor": "exclusiveGateway",
    "and": "parallelGateway",
    "store": "dataStoreReference",
    "object": "dataObjectReference",
}


def _flow_elements(d: Diagram, prefix: str, sub_content: dict[str, str]) -> list[str]:
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for i, (s, t, _) in enumerate(d.flows):
        fid = f"{prefix}_Flow_{i}"
        outgoing.setdefault(s, []).append(fid)
        incoming.setdefault(t, []).append(fid)
    xml = []
    for n in d.nodes:
        tag = BPMN_TAG[n.kind]
        if n.kind == "object":
            xml.append(f'<bpmn:dataObject id="{n.id}_Data"/>')
            xml.append(f'<bpmn:{tag} id="{n.id}" name="{escape(n.name)}" dataObjectRef="{n.id}_Data"/>')
            continue
        if n.kind == "store":
            xml.append(f'<bpmn:{tag} id="{n.id}" name="{escape(n.name)}"/>')
            continue
        inner = "".join(f"<bpmn:incoming>{f}</bpmn:incoming>" for f in incoming.get(n.id, []))
        inner += "".join(f"<bpmn:outgoing>{f}</bpmn:outgoing>" for f in outgoing.get(n.id, []))
        for k, (_, b) in enumerate(d.associations):
            if b == n.id:
                inner += f'<bpmn:property id="{n.id}_In{k}" name="__targetRef_placeholder"/>'
        for k, (a, b) in enumerate(d.associations):
            if b == n.id:
                inner += (
                    f'<bpmn:dataInputAssociation id="{prefix}_Assoc_{k}"><bpmn:sourceRef>{a}</bpmn:sourceRef>'
                    f"<bpmn:targetRef>{n.id}_In{k}</bpmn:targetRef></bpmn:dataInputAssociation>"
                )
            elif a == n.id:
                inner += f'<bpmn:dataOutputAssociation id="{prefix}_Assoc_{k}"><bpmn:targetRef>{b}</bpmn:targetRef></bpmn:dataOutputAssociation>'
        inner += sub_content.get(n.id, "")
        xml.append(f'<bpmn:{tag} id="{n.id}" name="{escape(n.name)}">{inner}</bpmn:{tag}>')
    for i, (s, t, label) in enumerate(d.flows):
        name = f' name="{escape(label)}"' if label else ""
        xml.append(f'<bpmn:sequenceFlow id="{prefix}_Flow_{i}"{name} sourceRef="{s}" targetRef="{t}"/>')
    return xml


def _di(d: Diagram, prefix: str, collapsed: set[str]) -> list[str]:
    xml = []
    for n in d.nodes:
        x, y, w, h = n.box
        extra = ' isExpanded="false"' if n.id in collapsed else ""
        xml.append(
            f'<bpmndi:BPMNShape id="{n.id}_di" bpmnElement="{n.id}"{extra}>'
            f'<dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}"/></bpmndi:BPMNShape>'
        )
    for i, (s, t, _) in enumerate(d.flows):
        pts = d.route(d.node(s), d.node(t))
        wps = "".join(f'<di:waypoint x="{px:.0f}" y="{py:.0f}"/>' for px, py in pts)
        xml.append(f'<bpmndi:BPMNEdge id="{prefix}_Flow_{i}_di" bpmnElement="{prefix}_Flow_{i}">{wps}</bpmndi:BPMNEdge>')
    for k, (a, b) in enumerate(d.associations):
        na, nb = d.node(a), d.node(b)
        ax = na.x + (na.size[0] / 2 if nb.x > na.x else -na.size[0] / 2)
        bx = nb.x - (nb.size[0] / 2 if nb.x > na.x else -nb.size[0] / 2)
        xml.append(
            f'<bpmndi:BPMNEdge id="{prefix}_Assoc_{k}_di" bpmnElement="{prefix}_Assoc_{k}">'
            f'<di:waypoint x="{ax:.0f}" y="{na.y:.0f}"/><di:waypoint x="{bx:.0f}" y="{nb.y:.0f}"/></bpmndi:BPMNEdge>'
        )
    return xml


def render_bpmn(main: Diagram, sub: Diagram, sub_id: str) -> str:
    sub_inner = "".join(_flow_elements(sub, "Sub", {}))
    process = _flow_elements(main, "Main", {sub_id: sub_inner})
    lanes = []
    for lane_id, label, _ in main.lanes:
        refs = "".join(f"<bpmn:flowNodeRef>{n.id}</bpmn:flowNodeRef>" for n in main.nodes if n.lane == lane_id and n.kind not in ("store", "object"))
        lanes.append(f'<bpmn:lane id="{lane_id}" name="{escape(label)}">{refs}</bpmn:lane>')
    top = MARGIN
    pool_h = main.height - 2 * MARGIN
    main_di = [
        f'<bpmndi:BPMNShape id="Pool_di" bpmnElement="Pool" isHorizontal="false">'
        f'<dc:Bounds x="{MARGIN}" y="{top}" width="{main.width - 2 * MARGIN:.0f}" height="{pool_h:.0f}"/></bpmndi:BPMNShape>'
    ]
    for lane_id, _, cols in main.lanes:
        main_di.append(
            f'<bpmndi:BPMNShape id="{lane_id}_di" bpmnElement="{lane_id}" isHorizontal="false">'
            f'<dc:Bounds x="{main.lane_x[lane_id]:.0f}" y="{top + LANE_HEAD}" width="{cols * COL_W}" '
            f'height="{pool_h - LANE_HEAD:.0f}"/></bpmndi:BPMNShape>'
        )
    main_di += _di(main, "Main", {sub_id})
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
            'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
            'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="GreenCityDefinitions" '
            'targetNamespace="https://github.com/Artem-Kornilov-pro/GreenCity" exporter="tools/docs/build_bpmn.py" exporterVersion="1.0">',
            f'<bpmn:collaboration id="Collaboration"><bpmn:participant id="Pool" name="{escape(main.pool or "")}" processRef="Process"/></bpmn:collaboration>',
            '<bpmn:process id="Process" name="От входного DXF до выходного DXF" isExecutable="false">',
            f'<bpmn:laneSet id="LaneSet">{"".join(lanes)}</bpmn:laneSet>',
            *process,
            "</bpmn:process>",
            f'<bpmndi:BPMNDiagram id="MainDiagram" name="{escape(main.title)}"><bpmndi:BPMNPlane id="MainPlane" bpmnElement="Collaboration">',
            *main_di,
            "</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>",
            f'<bpmndi:BPMNDiagram id="SubDiagram" name="{escape(sub.title)}"><bpmndi:BPMNPlane id="SubPlane" bpmnElement="{sub_id}">',
            *_di(sub, "Sub", set()),
            "</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>",
            "</bpmn:definitions>",
        ]
    )


# --- Процесс ------------------------------------------------------------------


def main_diagram() -> Diagram:
    U, B, L = "Lane_User", "Lane_Backend", "Lane_LLM"
    nodes = [
        Node("Start", "start", "Чертёж участка: DXF или папка DWG", U, 0, 0),
        Node("Upload", "task", "Загрузить чертёж в редактор", U, 0, 1),
        Node("Format", "xor", "Формат?", B, 0, 2),
        Node("Dwg", "task", "DWG → DXF (LibreDWG), склейка пачки в один документ", B, 1, 3),
        Node("FormatJoin", "xor", "", B, 0, 4),
        Node("Store", "task", "Сохранить исходный чертёж (id = sha256)", B, 0, 5),
        Node("Sources", "store", "Хранилище исходных чертежей", B, 2, 5),
        Node("Parse", "task", "Разбор слоёв: граница, зоны ограничений, здания, объекты → сцена", B, 0, 6),
        Node("Review", "task", "3D-сцена, зоны ограничений; параметры GreenPlan", U, 0, 7),
        Node("GreenPlan", "sub", "GreenPlan: озеленение по проектам-аналогам", B, 0, 8),
        Node("Corpus", "store", "Корпус 34 проектов, нормы НПА, ассортимент", B, 2, 8),
        Node("Check", "task", "Проверка результата, ручные правки на сцене", U, 0, 9),
        Node("TextEdit", "xor", "Правка текстом?", U, 0, 10),
        Node("Assistant", "llm", "ИИ-ассистент: список операций правки (Qwen3 235B)", L, 0, 11),
        Node("Apply", "task", "Применение операций планировщиком с проверкой норм", B, 0, 12),
        Node("EditJoin", "xor", "", U, 0, 13),
        Node("Fork", "and", "", U, 0, 14),
        Node("Export", "task", "Экспорт DXF поверх исходника: слои NEW_*, USER_*, XDATA с id", B, 0, 15),
        Node("Explain", "task", "Объяснения посадок со ссылками на НПА (JSON, CSV)", B, 1, 15),
        Node("Report", "llm", "Текст-обоснование решений (YandexGPT 5.1 Pro)", L, 0, 15),
        Node("Note", "task", "Пояснительная записка (DOCX)", B, 2, 16),
        Node("Join", "and", "", B, 0, 17),
        Node("End", "end", "Выходной DXF, файл объяснений, записка", U, 0, 18),
    ]
    flows = [
        ("Start", "Upload", ""),
        ("Upload", "Format", ""),
        ("Format", "Dwg", "DWG"),
        ("Format", "FormatJoin", "DXF"),
        ("Dwg", "FormatJoin", ""),
        ("FormatJoin", "Store", ""),
        ("Store", "Parse", ""),
        ("Parse", "Review", ""),
        ("Review", "GreenPlan", ""),
        ("GreenPlan", "Check", ""),
        ("Check", "TextEdit", ""),
        ("TextEdit", "Assistant", "да"),
        ("TextEdit", "EditJoin", "нет"),
        ("Assistant", "Apply", ""),
        ("Apply", "EditJoin", ""),
        ("EditJoin", "Fork", ""),
        ("Fork", "Export", ""),
        ("Fork", "Explain", ""),
        ("Fork", "Report", ""),
        ("Report", "Note", ""),
        ("Export", "Join", ""),
        ("Explain", "Join", ""),
        ("Note", "Join", ""),
        ("Join", "End", ""),
    ]
    associations = [("Store", "Sources"), ("Corpus", "GreenPlan")]
    return Diagram(
        "Main",
        "GreenCity: от входного DXF до выходного DXF",
        [(U, "Пользователь (редактор)", 1), (B, "Бэкенд GreenCity", 3), (L, "Yandex AI Studio (LLM)", 1)],
        nodes,
        flows,
        associations,
        pool="GreenCity: от входного DXF до выходного DXF и файла объяснений",
    )


def greenplan_diagram() -> Diagram:
    G = "Lane_GreenPlan"
    nodes = [
        Node("GP_Start", "start", "Сцена участка и параметры GreenPlan", G, 0, 0),
        Node("GP_Clean", "task", "Убрать прошлый результат GreenPlan", G, 0, 1),
        Node("GP_Impr", "xor", "Благоустройство?", G, 0, 2),
        Node("GP_Paths", "task", "Дорожки, освещение, скамейки с урнами", G, 1, 3),
        Node("GP_ImprJoin", "xor", "", G, 0, 4),
        Node("GP_Features", "task", "Признаки участка: площадь, пригодная доля, состав зон, тип территории", G, 0, 5),
        Node("GP_Retrieval", "task", "Поиск аналогов: k ближайших по косинусному сходству", G, 0, 6),
        Node("GP_Corpus", "store", "Корпус: 14 реальных + 20 эталонов", G, 2, 6),
        Node("GP_Zones", "task", "Разбиение на зоны: у зданий, вдоль дорожек, по краю, открытые", G, 0, 7),
        Node("GP_Assign", "task", "Стиль участка и приёмы зон: взвешенное голосование аналогов", G, 0, 8),
        Node("GP_Species", "task", "Подбор видов: ассортимент Москвы, 515-ПП, без 369-ПП", G, 0, 9),
        Node("GP_Assortment", "store", "Ассортимент Москвы, 369-ПП, 515-ПП", G, 1, 9),
        Node("GP_Place", "task", "Расстановка: ряды, заливка, группы; отступ по норме в каждой точке", G, 0, 10),
        Node("GP_Norms", "store", "Отступы: СП 42, 743-ПП, МГСН 1.02-02, СП 82", G, 2, 10),
        Node("GP_Rejected", "object", "Журнал отклонённых точек", G, 1, 11),
        Node("GP_Lawn", "task", "Газон на свободной от покрытий и клумб земле", G, 0, 11),
        Node("GP_Check", "task", "Независимая проверка нарушений, ведомость", G, 0, 12),
        Node("GP_End", "end", "Сцена с посадкой, решения по зонам", G, 0, 13),
    ]
    flows = [
        ("GP_Start", "GP_Clean", ""),
        ("GP_Clean", "GP_Impr", ""),
        ("GP_Impr", "GP_Paths", "да"),
        ("GP_Impr", "GP_ImprJoin", "нет"),
        ("GP_Paths", "GP_ImprJoin", ""),
        ("GP_ImprJoin", "GP_Features", ""),
        ("GP_Features", "GP_Retrieval", ""),
        ("GP_Retrieval", "GP_Zones", ""),
        ("GP_Zones", "GP_Assign", ""),
        ("GP_Assign", "GP_Species", ""),
        ("GP_Species", "GP_Place", ""),
        ("GP_Place", "GP_Lawn", ""),
        ("GP_Lawn", "GP_Check", ""),
        ("GP_Check", "GP_End", ""),
    ]
    associations = [
        ("GP_Corpus", "GP_Retrieval"),
        ("GP_Assortment", "GP_Species"),
        ("GP_Norms", "GP_Place"),
        ("GP_Place", "GP_Rejected"),
    ]
    return Diagram("Sub", "Подпроцесс GreenPlan", [(G, "GreenPlan", 3)], nodes, flows, associations)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    main_d, sub_d = main_diagram(), greenplan_diagram()
    main_d.layout()
    sub_d.layout()
    (OUT / "greencity_process.svg").write_text(render_svg(main_d), encoding="utf-8")
    (OUT / "greenplan_subprocess.svg").write_text(render_svg(sub_d), encoding="utf-8")
    (OUT / "greencity_process.bpmn").write_text(render_bpmn(main_d, sub_d, "GreenPlan"), encoding="utf-8")
    print(f"готово: {OUT}")


if __name__ == "__main__":
    main()
