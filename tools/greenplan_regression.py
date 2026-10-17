"""
Проверка GreenPlan на деградацию: тестовые участки (locations/location_test)
и примеры (examples/) разбираются и озеленяются GreenPlan с параметрами по
умолчанию кодом из git-ревизии (по умолчанию main, во временном worktree) и
текущим кодом; посадки сравниваются целиком -- координаты, виды, решения по
зонам и проекты-аналоги.

    .venv/bin/python tools/greenplan_regression.py               # против main
    .venv/bin/python tools/greenplan_regression.py --base v1.0.1
    .venv/bin/python tools/greenplan_regression.py a.dxf b.dxf   # свои файлы

Код возврата 1, если хоть один план отличается.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ("locations/location_test/*/[0-9]*.dxf", "examples/*/*.dxf")


def _plan(code_root: str, path: str) -> dict:
    """Рабочий процесс: план GreenPlan для одного файла кодом из code_root."""
    from core.schemas import Scene
    from exchange.dxf_parser import complete_scene, parse_dxf_file
    from greenplan.pipeline import run_greenplan

    scene = Scene.model_validate(complete_scene(parse_dxf_file(path)))
    run = run_greenplan(scene)
    plants = sorted(
        (o.type, round(o.position.x, 3), round(o.position.z, 3), str(o.metadata.get("species"))) for o in run.new_plants
    )
    assignments = sorted(
        (a.zone_id, a.pattern_id, str(a.source_project), tuple(a.tree_species or ()), tuple(a.bush_species or ()))
        for a in run.assignments
    )
    return {"plants": plants, "assignments": assignments, "notes": run.notes}


def _dump(code_root: str, files: list[str], out: Path) -> None:
    sys.path[:0] = [str(Path(code_root) / "backend"), str(Path(code_root) / "parser"), code_root]
    import logging

    logging.disable(logging.WARNING)
    results = {}
    for path in files:
        try:
            results[path] = {"plan": json.loads(json.dumps(_plan(code_root, path), ensure_ascii=False, default=str)), "error": None}
        except Exception as e:  # noqa: BLE001 -- ошибка -- тоже результат
            results[path] = {"plan": None, "error": repr(e)}
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


def _run_dump(code_root: Path, files: list[str], out: Path) -> dict:
    # Отдельный процесс: модули backend из двух ревизий не должны смешаться.
    subprocess.run([sys.executable, __file__, "--dump", str(code_root), str(out), *files], check=True, cwd=code_root)
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--dump":
        _dump(sys.argv[2], sys.argv[4:], Path(sys.argv[3]))
        return 0

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="DXF для проверки (по умолчанию -- тестовые участки и примеры)")
    ap.add_argument("--base", default="main", help="git-ревизия, с которой сравнивать (по умолчанию main)")
    args = ap.parse_args()

    files = [str(Path(f).resolve()) for f in args.files] or sorted(str(p) for pattern in CORPUS for p in ROOT.glob(pattern))
    with tempfile.TemporaryDirectory(prefix="greenplan-regression-") as tmp:
        worktree = Path(tmp) / "base"
        subprocess.run(["git", "worktree", "add", "--detach", str(worktree), args.base], cwd=ROOT, check=True, capture_output=True)
        try:
            print(f"GreenPlan на {len(files)} участках: {args.base} и текущий код…", flush=True)
            base = _run_dump(worktree, files, Path(tmp) / "base.json")
            head = _run_dump(ROOT, files, Path(tmp) / "head.json")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT, capture_output=True)

    changed = 0
    for path in files:
        a, b = base[path], head[path]
        name = os.path.relpath(path, ROOT)
        if a != b:
            changed += 1
            if a["error"] or b["error"]:
                print(f"ОШИБКА {name}: {a['error']} -> {b['error']}")
                continue
            print(f"ИЗМЕНЕНО {name}")
            for key in ("plants", "assignments", "notes"):
                if a["plan"][key] != b["plan"][key]:
                    print(f"    {key}: {len(a['plan'][key])} -> {len(b['plan'][key])} (или содержимое)")
    print(f"Совпало {len(files) - changed} из {len(files)}.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
