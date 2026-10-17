"""tools/audit_references.py (issue #113): аудит эталонов корпуса теми же
нормами, что посадки GreenPlan."""

import importlib.util
from collections import Counter

from core.paths import REPO_ROOT

_spec = importlib.util.spec_from_file_location("audit_references", REPO_ROOT / "tools" / "audit_references.py")
audit_references = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_references)


def test_only_real_projects_are_audited():
    projects = audit_references._projects()
    assert projects and not any(slug.endswith("_primer") for slug in projects)
    assert all((REPO_ROOT / "locations" / slug / f"{slug}.dxf").exists() for slug in projects)


def test_audit_counts_plants_that_break_the_setbacks():
    row = audit_references.audit("13_kharkovsky_proezd")
    assert row["trees"] + row["bushes"] > 0
    assert 0 <= row["violating"] <= row["trees"] + row["bushes"]
    assert row["share"] == row["violating"] / (row["trees"] + row["bushes"])
    assert sum(row["by_zone"].values()) >= row["violating"]


def test_table_totals():
    rows = [
        {"slug": "a", "trees": 3, "bushes": 1, "violating": 2, "share": 0.5, "by_zone": Counter({"здания": 2})},
        {"slug": "b", "trees": 1, "bushes": 5, "violating": 0, "share": 0.0, "by_zone": Counter()},
    ]
    text = audit_references.table(rows)
    assert "| a | 3 | 1 | 2 | 50% | здания — 2 |" in text
    assert "| **Итого** | 4 | 6 | 2 | 20% | |" in text
