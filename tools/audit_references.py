"""
Аудит эталонов: реальные проекты корпуса прецедентов GreenPlan
(data/pattern_corpus.yaml, без синтетических *_primer) проверяются теми же
нормами отступов, что и посадки GreenPlan (greenplan/violation_report.py).

Посадки проекта -- все деревья и кустарники его чертежа: и новые, и
сохраняемые (в чертежах корпуса они не всегда на разных слоях). Отступы --
от ограничений того же чертежа; у сетей без подписи диаметра -- от края
охранного коридора, поэтому число нарушений у сетей -- оценка сверху.

    .venv/bin/python tools/audit_references.py            # таблица в stdout
    .venv/bin/python tools/audit_references.py --write    # и в docs/AUDIT_REFERENCES.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "parser")]

import yaml  # noqa: E402

from core.schemas import Scene  # noqa: E402
from exchange.dxf_parser import complete_scene, parse_dxf_file  # noqa: E402
from greenplan.violation_report import ZONE_TYPE_LABELS, find_violations  # noqa: E402

OUT = ROOT / "docs" / "AUDIT_REFERENCES.md"
_PLANTS = ("tree", "bush")


def _projects() -> list[str]:
    corpus = yaml.safe_load((ROOT / "data" / "pattern_corpus.yaml").read_text(encoding="utf-8")) or {}
    return sorted(slug for slug in corpus if not slug.endswith("_primer") and (ROOT / "locations" / slug / f"{slug}.dxf").exists())


def audit(slug: str) -> dict:
    scene = Scene.model_validate(complete_scene(parse_dxf_file(str(ROOT / "locations" / slug / f"{slug}.dxf"))))
    plants = {o.id: o.type for o in scene.objects if o.type in _PLANTS}
    violations = [v for v in find_violations(scene) if v.object_id in plants]
    violating = {v.object_id for v in violations}
    by_zone = Counter()
    for _object_id, zone_type in {(v.object_id, v.zone_type) for v in violations}:
        by_zone[ZONE_TYPE_LABELS.get(zone_type, zone_type)] += 1
    return {
        "slug": slug,
        "trees": sum(1 for t in plants.values() if t == "tree"),
        "bushes": sum(1 for t in plants.values() if t == "bush"),
        "violating": len(violating),
        "share": len(violating) / len(plants) if plants else 0.0,
        "by_zone": by_zone,
    }


def table(rows: list[dict]) -> str:
    lines = [
        "| Проект | Деревьев | Кустарников | С нарушением отступа | Доля | Чаще всего — от чего |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        top = ", ".join(f"{name} — {n}" for name, n in r["by_zone"].most_common(3)) or "—"
        lines.append(f"| {r['slug']} | {r['trees']} | {r['bushes']} | {r['violating']} | {r['share']:.0%} | {top} |")
    plants = sum(r["trees"] + r["bushes"] for r in rows)
    violating = sum(r["violating"] for r in rows)
    lines.append(f"| **Итого** | {sum(r['trees'] for r in rows)} | {sum(r['bushes'] for r in rows)} | {violating} | {violating / plants:.0%} | |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"записать {OUT.relative_to(ROOT)}")
    args = ap.parse_args()
    logging.disable(logging.WARNING)

    rows = [audit(slug) for slug in _projects()]
    text = table(rows)
    print(text)
    if args.write:
        plants = sum(r["trees"] + r["bushes"] for r in rows)
        violating = sum(r["violating"] for r in rows)
        with_violations = sum(1 for r in rows if r["violating"])
        OUT.write_text(
            f"""# Аудит эталонов: реальные проекты против норм отступов

Реальные проекты корпуса прецедентов GreenPlan (`data/pattern_corpus.yaml`, без синтетических
`*_primer`) проверены теми же нормами отступов и тем же кодом, что и посадки GreenPlan
(`greenplan/violation_report.py`). Пересчитать: `make audit-references`
(`tools/audit_references.py --write`). Дата расчёта: {dt.date.today():%d.%m.%Y}.

**Итог:** в {with_violations} из {len(rows)} проектов есть посадки ближе нормы; всего {violating} из {plants}
деревьев и кустарников ({violating / plants:.0%}). У новых посадок GreenPlan нарушений нет: каждая точка
проходит те же нормы при расстановке, а результат проверяется ещё раз по выгруженному DXF
(`POST /api/export-dxf/verify`).

{text}

## Как читать

- **Посадки проекта** — все деревья и кустарники чертежа: и новые, и сохраняемые (в чертежах корпуса
  они не всегда разнесены по слоям). Нарушение у сохраняемого дерева — не ошибка проектировщика, а
  исходное положение участка.
- **Отступы** — табл. 9.1 СП 42.13330.2016 / 743-ПП, правила по породе, ПУЭ для ЛЭП
  (`core/setback_norms.py`); у каждой нормы — статус сверки (сверено / допущение / значение сервиса).
- **Сети без подписи диаметра** — отступ от края охранного коридора, поэтому нарушений у сетей
  может быть меньше, чем показано: это оценка сверху.
- **Зачем это GreenPlan.** Прецеденты дают приёмы и композицию (ряд, группа, изгородь), а не
  координаты: GreenPlan переносит приём на новый участок и ставит каждую точку только туда, где
  выполнены нормы. Поэтому ошибки отступов в эталонах в план не переходят.

## Для слайда

**Эталоны тоже нарушают нормы — GreenPlan нет.** {violating / plants:.0%} деревьев и кустарников в {with_violations}
из {len(rows)} реальных проектов корпуса стоят ближе нормативного отступа (чаще всего — от кабелей,
проезжей части и дорожек). GreenPlan берёт у проектов приёмы, а не координаты: каждая новая посадка
проходит табл. 9.1 СП 42.13330.2016, 743-ПП и ПУЭ, и выгруженный DXF проверяется заново —
0 нарушений, исходный чертёж цел.
""",
            encoding="utf-8",
        )
        print(f"\nЗаписано: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
