"""
Оценка ИИ-ассистента (правка плана текстом) на НАСТОЯЩЕЙ модели: набор
реалистичных просьб на трёх участках, у каждой -- объективная проверка
результата по сцене (что добавлено/удалено, какие виды, нарушения норм у
новых объектов), а не "на глаз".

ВНИМАНИЕ: каждый случай -- платный запрос к LLM (Yandex AI Studio, ключ из
.env), ~10-20 тыс. токенов. В тесты не входит, запускается вручную:

    .venv/bin/python tools/ai_editor_eval/run.py              # все случаи
    .venv/bin/python tools/ai_editor_eval/run.py yard_lindens  # выборочно

Итог -- таблица в консоли и подробный JSON рядом (last_run.json, в .gitignore).
"""

from __future__ import annotations

import glob
import json
import logging
import math
import re
import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
warnings.filterwarnings("ignore")

from cases import CASES, Case  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from core.schemas import Point2, RestrictionZone, Scene  # noqa: E402
from exchange.dxf_parser import parse_dxf_file  # noqa: E402
from greenplan.violation_report import find_violations  # noqa: E402
from text_editor.operations import ChatTurn  # noqa: E402
from text_editor.service import edit_scene_with_text  # noqa: E402

SCENES = {
    "yard": "locations/location_old/02_*/*.dxf",
    "play": "locations/23_*/*.dxf",
    "real": "locations/12_*/*.dxf",
}


class TokenCounter(logging.Handler):
    """Токены и время ответа -- из лога llm_client ("токены: вход N, выход M")."""

    def __init__(self):
        super().__init__()
        self.input = self.output = 0
        self.plans: list[str] = []

    def emit(self, record):
        message = record.getMessage()
        m = re.search(r"токены: вход (\d+), выход (\d+)", message)
        if m:
            self.input += int(m.group(1))
            self.output += int(m.group(2))
        if message.startswith("план: "):
            self.plans.append(message[len("план: "):])


def load_scene(key: str) -> Scene:
    path = glob.glob(str(ROOT / SCENES[key]))[0]
    return Scene.model_validate(parse_dxf_file(path))


def with_selection(scene: Scene, polygon: list[tuple[float, float]]) -> Scene:
    """Зона, выделенная мышкой, -- как её добавляет фронтенд."""
    zone = RestrictionZone(
        id="selection",
        type="selection",
        name="Выделение",
        polygon=[Point2(x=x, z=z) for x, z in polygon],
        severity="allowed",
        minDistance=0,
        message="Зона, выделенная вручную",
    )
    return scene.model_copy(update={"restrictions": [*scene.restrictions, zone]})


class Outcome:
    """Что сделала правка -- для проверок в cases.py."""

    def __init__(self, before: Scene, result):
        self.before, self.result, self.after = before, result, result.scene
        before_ids = {o.id for o in before.objects}
        after_ids = {o.id for o in self.after.objects}
        self.added = [o for o in self.after.objects if o.id not in before_ids]
        self.removed = [o for o in before.objects if o.id not in after_ids]
        old = {o.id: o for o in before.objects}
        self.changed = [o for o in self.after.objects if o.id in old and o != old[o.id]]
        added_ids = {o.id for o in self.added} | {o.id for o in self.changed}
        self.violations = [v for v in find_violations(self.after) if v.object_id in added_ids]

    def added_of(self, type_: str):
        return [o for o in self.added if o.type == type_]

    def count(self, scene: Scene, type_: str, species_prefix: str | None = None) -> int:
        return sum(
            1 for o in scene.objects
            if o.type == type_ and (species_prefix is None or str(o.metadata.get("species", "")).startswith(species_prefix))
        )

    def zone(self, zone_type: str) -> Polygon:
        z = next(z for z in self.after.restrictions if z.type == zone_type)
        return Polygon([(p.x, p.z) for p in z.polygon])

    def entrances(self):
        return [o for o in self.before.objects if o.type == "entrance"]


def near(obj, x: float, z: float) -> float:
    return math.hypot(obj.position.x - x, obj.position.z - z)


def run_case(case: Case, scenes: dict[str, Scene], counter: TokenCounter) -> dict:
    scene = scenes[case.scene]
    if case.setup:
        scene = case.setup(scene)
    history: list[ChatTurn] = []
    started = time.monotonic()
    tokens_before = (counter.input, counter.output)
    counter.plans = []
    outcomes = []
    try:
        for instruction in case.turns:
            before = scene
            result = edit_scene_with_text(scene, instruction, history)
            outcomes.append(Outcome(before, result))
            history.append(ChatTurn(instruction=instruction, explanation=result.explanation, applied=result.applied[:4], added_ids=result.added_ids))
            scene = result.scene
        problems = case.check(outcomes)
        error = None
    except Exception as e:  # noqa: BLE001 -- оценка, а не прод: любой сбой -- в отчёт
        problems, error = [f"{type(e).__name__}: {e}"], str(e)
    last = outcomes[-1] if outcomes else None
    return {
        "id": case.id,
        "scene": case.scene,
        "turns": case.turns,
        "ok": not problems,
        "problems": problems,
        "error": error,
        "seconds": round(time.monotonic() - started, 1),
        "tokens_in": counter.input - tokens_before[0],
        "tokens_out": counter.output - tokens_before[1],
        "plans": counter.plans,
        "explanation": last.result.explanation if last else None,
        "applied": last.result.applied if last else [],
        "rejected": last.result.rejected if last else [],
        "warnings": last.result.warnings[:6] if last else [],
        "new_violations": len(last.violations) if last else None,
        "greenplan": last.result.greenplan.model_dump() if last and last.result.greenplan else None,
    }


def main(selected: list[str]) -> None:
    counter = TokenCounter()
    log = logging.getLogger("greencity.llm")
    log.setLevel(logging.INFO)
    log.addHandler(counter)
    logging.getLogger().setLevel(logging.WARNING)

    cases = [c for c in CASES if not selected or c.id in selected]
    scenes = {key: load_scene(key) for key in {c.scene for c in cases}}
    out = Path(__file__).with_name("last_run.json")
    results = []
    for case in cases:
        r = run_case(case, scenes, counter)
        results.append(r)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {r['id']:<28} {r['seconds']:>5.1f} с  {r['tokens_in']:>6}+{r['tokens_out']:<5} {'; '.join(r['problems'])[:150]}", flush=True)

    passed = sum(r["ok"] for r in results)
    print(f"\nИтог: {passed}/{len(results)}, токенов: вход {counter.input}, выход {counter.output}")
    print(f"Подробно: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
