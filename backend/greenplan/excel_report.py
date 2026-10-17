"""
Excel-книга по плану озеленения (issue #111) -- для проектировщика и
эксперта, которым удобнее таблица, чем JSON:

* «Ведомость» -- посадки по видам (форма 9 ГОСТ 21.508), газон, деревья
  «условно, с корнезащитой», благоустройство;
* «Посадки» -- каждая новая посадка: вид, координаты в сцене и в чертеже,
  приём и аналог, обоснование, статус;
* «Проверки» -- каждая посадка x каждое ограничение рядом: фактическое
  расстояние, норма, выполнена ли, НПА и статус нормы;
* «Отклонено» -- места, которые расстановка не взяла, с причиной;
* «Запрет посадки» -- полосы вокруг ограничений и «не проверялось нормами»;
* «Сведения» -- SHA-256 исходника и версии правил.

Те же данные, что в файле объяснений (greenplan/explanations.py), -- одна
правда в JSON, CSV и Excel.
"""

from __future__ import annotations

import io
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.plant_catalog import catalog_by_id
from core.schemas import Scene
from core.setback_norms import NORM_STATUS_LABELS
from core.versions import versions
from greenplan.assortment_report import improvements_assortment, lawn_assortment, summarize_assortment
from greenplan.explanations import PlantExplanation, RejectionStats, not_checked, restricted_zones

_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="DCEFE0")
_BAD_FILL = PatternFill("solid", fgColor="F8D7D3")
_MAX_WIDTH = 60


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for cell in ws[1]:
        cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        ws.append(["" if value is None else value for value in row])
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)), start=1):
        width = max((len(str(cell.value)) for cell in column if cell.value is not None), default=8)
        ws.column_dimensions[get_column_letter(index)].width = min(_MAX_WIDTH, max(8, width + 2))
    return ws


def _status(status) -> str:
    return NORM_STATUS_LABELS.get(status, "правило планировщика") if status else ""


def explanations_xlsx(scene: Scene, plants: list[PlantExplanation], rejections: RejectionStats) -> bytes:
    wb = Workbook()

    # Ведомость: по виду -- из сцены (то же, что /generate и записка), с
    # отдельной строкой деревьев с корнезащитой.
    generated = [o for o in scene.objects if o.metadata.get("generated")]
    rows = [[r.category, r.species, r.count, r.unit] for r in summarize_assortment(generated, catalog_by_id())]
    rows += [[r.category, r.species, r.count, r.unit] for r in lawn_assortment(scene.lawns)]
    barrier = Counter(o.metadata.get("species") or o.type for o in generated if o.metadata.get("rootBarrier"))
    rows += [["в т. ч. условно, с корнезащитой", species, count, "шт."] for species, count in barrier.most_common()]
    rows += [[r.category, r.species, r.count, r.unit] for r in improvements_assortment(scene)]
    _sheet(wb, "Ведомость", ["Категория", "Вид / позиция", "Количество", "Ед."], rows, first=True)

    _sheet(
        wb,
        "Посадки",
        ["id", "Статус", "Тип", "Вид", "Слой DXF", "x, м", "z, м", "X чертежа", "Y чертежа", "Приём", "Проект-аналог", "Обоснование", "Основание вида"],
        [
            [p.id, p.status, p.type, p.species, p.dxf_layer, p.x, p.z, p.dxf_x, p.dxf_y, p.pattern, p.source_project, p.reason, p.species_basis]
            for p in plants
        ],
    )

    checks = _sheet(
        wb,
        "Проверки",
        ["id посадки", "Вид", "Ограничение", "Расстояние, м", "Норма, м", "Выполнено", "НПА и пункт", "Статус нормы"],
        [
            [p.id, p.species, c.what, c.distance_m, c.required_m, "да" if c.ok else "НЕТ", c.norm, _status(c.norm_status)]
            for p in plants
            for c in p.constraints
        ],
    )
    for row in checks.iter_rows(min_row=2):
        if row[5].value == "НЕТ":
            for cell in row:
                cell.fill = _BAD_FILL

    _sheet(
        wb,
        "Отклонено",
        ["x, м", "z, м", "Тип", "Вид", "Зона", "Приём", "Причина", "Расстояние, м", "Норма, м", "НПА и пункт"],
        [[r.x, r.z, r.type, r.species, r.zone_kind, r.pattern, r.reason, r.distance_m, r.required_m, r.norm] for r in rejections.detailed],
    )

    zones = restricted_zones(scene)
    rows = [
        [z.zone_id, z.what, z.tree_setback_m, z.tree_norm, _status(z.tree_norm_status), z.bush_setback_m, z.bush_norm, _status(z.bush_norm_status)]
        for z in zones
    ]
    rows += [["", f"не проверялось нормами: {row['what']}", "", row["reason"], "", "", "", ""] for row in not_checked(scene)]
    _sheet(
        wb,
        "Запрет посадки",
        ["id зоны", "Ограничение", "Дерево, м", "Норма (дерево)", "Статус", "Кустарник, м", "Норма (кустарник)", "Статус"],
        rows,
    )

    info = [["Исходный чертёж, SHA-256", scene.meta.sourceSha256 or "не известен"]]
    info += [[f"Версия: {name}", value] for name, value in versions().items()]
    info += [
        ["Новых посадок", len(plants)],
        ["Проверок отступов", sum(len(p.constraints) for p in plants)],
        ["Не выполнено", sum(1 for p in plants for c in p.constraints if not c.ok)],
        ["Отклонено мест", rejections.total],
    ]
    _sheet(wb, "Сведения", ["Показатель", "Значение"], info)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
