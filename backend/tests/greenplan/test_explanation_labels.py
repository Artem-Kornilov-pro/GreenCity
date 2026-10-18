"""Зазоры между посадками в объяснениях: по-русски и со статусом
«правило планировщика», а не с пустой
ячейкой в таблице проверок."""

import io

from openpyxl import load_workbook

from greenplan.excel_report import explanations_xlsx
from greenplan.explanations import PLANNER_RULE, PLANNER_STATUS_LABEL, RejectionStats, explain_plants
from greenplan.options import GreenPlanOptions
from greenplan.pipeline import run_greenplan
from helpers import make_scene


def _explained():
    scene = make_scene()
    scene.meta.origin = {"x": 0.0, "y": 0.0}
    run = run_greenplan(scene, GreenPlanOptions())
    return run.scene, explain_plants(run.scene, run.assignments)


def test_spacing_to_other_plants_is_named_in_russian():
    _scene, plants = _explained()
    spacing = [c for p in plants for c in p.constraints if c.norm == PLANNER_RULE]
    assert spacing, "в густой посадке должны быть зазоры до соседних посадок"
    for c in spacing:
        label = c.what.split()[0]
        assert label in {"дерево", "кустарник", "живая", "подъезд"}, c.what
        assert c.norm_status is None


def test_excel_checks_sheet_has_no_blank_status():
    scene, plants = _explained()
    sheet = load_workbook(io.BytesIO(explanations_xlsx(scene, plants, RejectionStats())))["Проверки"]
    rows = list(sheet.iter_rows(values_only=True))
    status = rows[0].index("Статус нормы")
    assert all(row[status] for row in rows[1:])
    assert any(row[status] == PLANNER_STATUS_LABEL for row in rows[1:])
