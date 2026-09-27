"""
Примеры результата для документации: участки из датасета кейса проходят тот же
путь, что и в редакторе, через HTTP API бэкенда -- загрузка DXF, GreenPlan,
экспорт DXF поверх исходника, файл объяснений и пояснительная записка. LLM не
вызывается (текст-обоснование в записку не входит).

    .venv/bin/python tools/build_examples.py            # все участки EXAMPLES
    .venv/bin/python tools/build_examples.py 07_nizhnie_polya

Результат -- examples/<участок>/.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ["07_nizhnie_polya", "06_kamchatskaya_ulitsa", "12_natashinsky_proezd"]
OUT_DIR = ROOT / "examples"


def build(client, slug: str) -> dict:
    source = ROOT / "locations" / slug / f"{slug}.dxf"
    out = OUT_DIR / slug
    out.mkdir(parents=True, exist_ok=True)

    with source.open("rb") as f:
        res = client.post("/api/parse", files={"file": (source.name, f, "application/dxf")})
    res.raise_for_status()
    scene = res.json()

    res = client.post("/api/greenplan/generate", json={"scene": scene})
    res.raise_for_status()
    plan = res.json()

    res = client.post("/api/export-dxf", json=plan["scene"])
    res.raise_for_status()
    assert res.headers["X-GreenCity-Export"] == "overlay", "экспорт должен идти поверх исходника"
    (out / f"{slug}_greenplan.dxf").write_bytes(res.content)

    body = {"scene": plan["scene"], "assignments": plan["assignments"], "rejections": plan["rejections"]}
    for fmt in ("json", "csv"):
        res = client.post(f"/api/greenplan/explanations?format={fmt}", json=body)
        res.raise_for_status()
        (out / f"{slug}_explanations.{fmt}").write_bytes(res.content)

    res = client.post(
        "/api/greenplan/document",
        json={"scene": plan["scene"], "assignments": plan["assignments"], "title": slug, "notes": plan["notes"]},
    )
    res.raise_for_status()
    (out / f"{slug}_note.docx").write_bytes(res.content)

    new_objects = [o for o in plan["scene"]["objects"] if o["metadata"].get("generated")]
    new_ids = {o["id"] for o in new_objects}
    summary = {
        "site": slug,
        "source_dxf": f"locations/{slug}/{slug}.dxf",
        "site_style": plan["assignments"][0]["site_style"] if plan["assignments"] else None,
        "lead_project": plan["assignments"][0]["lead_project"] if plan["assignments"] else None,
        "zones": len(plan["assignments"]),
        "new_trees": sum(o["type"] == "tree" for o in new_objects),
        "new_bushes": sum(o["type"] == "bush" for o in new_objects),
        "new_lawn_sqm": round(sum(lawn["area_sqm"] for lawn in plan["scene"]["lawns"] if lawn["status"] == "new")),
        "violations_new_objects": sum(1 for v in plan["violations"] if v["object_id"] in new_ids),
        "rejected_by_norm": plan["rejections"]["total"],
        "assortment": [
            {"category": row["category"], "species": row["species"], "count": row["count"], "unit": row["unit"]}
            for row in plan["assortment"]
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    slugs = sys.argv[1:] or EXAMPLES
    for key in ("YANDEX_CLOUD_API_KEY", "GEMINI_API_KEY"):
        os.environ.pop(key, None)
    with tempfile.TemporaryDirectory() as sources:
        os.environ["GREENCITY_SOURCES_DIR"] = sources
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi.testclient import TestClient

        from main import app

        # Без lifespan: MongoDB и Redis для этих запросов не нужны.
        client = TestClient(app)
        for slug in slugs:
            summary = build(client, slug)
            print(json.dumps({k: v for k, v in summary.items() if k != "assortment"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
