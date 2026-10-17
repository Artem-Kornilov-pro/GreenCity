"""
Проверка разбора чертежей на деградацию: каждый DXF корпуса разбирается кодом
из git-ревизии (по умолчанию main, во временном worktree) и текущим кодом --
тем же путём, что /api/parse, -- и сцены сравниваются. Новые поля, которые
только добавляют сведения (meta.layers, meta.layerMap, meta.warnings,
layerKind и voltageKv зоны), не считаются отличием.

    .venv/bin/python tools/parser_regression.py                 # весь корпус против main
    .venv/bin/python tools/parser_regression.py --base v1.0.1   # против тега
    .venv/bin/python tools/parser_regression.py a.dxf b.dxf     # свои файлы

Корпус -- locations/**/*.dxf и examples/**/*.dxf; файлы корпуса, которых в
ревизии ещё нет, пропускаются -- сравнивать их не с чем. Код возврата 1, если
хоть одна сцена отличается; отличия печатаются по ключам сцены.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDED_META = {"layers", "layerMap", "warnings", "boundaryCandidates", "geo"}


def _parse(args: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    """Рабочий процесс: разбор одного файла кодом из code_root."""
    code_root, path = args
    sys.path[:0] = [str(Path(code_root) / "backend"), code_root]
    import logging

    logging.disable(logging.WARNING)
    from exchange.dxf_parser import complete_scene, parse_dxf_file

    try:
        return path, complete_scene(parse_dxf_file(path)), None
    except Exception as e:  # noqa: BLE001 -- ошибка разбора -- тоже результат
        return path, None, repr(e)


def _dump(code_root: str, files: list[str], out: Path) -> None:
    with ProcessPoolExecutor() as pool:
        results = {path: {"scene": scene, "error": error} for path, scene, error in pool.map(_parse, [(code_root, f) for f in files])}
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


def _run_dump(code_root: Path, files: list[str], out: Path) -> dict:
    # Отдельный процесс: модули backend из двух ревизий не должны смешаться.
    subprocess.run([sys.executable, __file__, "--dump", str(code_root), str(out), *files], check=True, cwd=code_root)
    return json.loads(out.read_text(encoding="utf-8"))


def _without_added_fields(scene: dict) -> dict:
    scene = json.loads(json.dumps(scene))
    for key in ADDED_META:
        scene["meta"].pop(key, None)
    for zone in scene.get("restrictions", []):
        zone.pop("layerKind", None)
        zone.pop("voltageKv", None)
    return scene


def _describe(base: dict, head: dict) -> list[str]:
    lines = []
    for key in sorted(set(base) | set(head)):
        a, b = base.get(key), head.get(key)
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list):
            lines.append(f"{key}: {len(a)} -> {len(b)} (или содержимое)")
        elif isinstance(a, dict) and isinstance(b, dict):
            changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            lines.append(f"{key}: {', '.join(f'{k} {a.get(k)!r} -> {b.get(k)!r}' for k in changed)}")
        else:
            lines.append(f"{key}: {a!r} -> {b!r}")
    return lines


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--dump":
        _dump(sys.argv[2], sys.argv[4:], Path(sys.argv[3]))
        return 0

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="DXF для проверки (по умолчанию -- весь корпус)")
    ap.add_argument("--base", default="main", help="git-ревизия, с которой сравнивать (по умолчанию main)")
    args = ap.parse_args()

    files = [str(Path(f).resolve()) for f in args.files] or sorted(
        str(p) for pattern in ("locations/**/*.dxf", "examples/**/*.dxf") for p in ROOT.glob(pattern)
    )
    with tempfile.TemporaryDirectory(prefix="parser-regression-") as tmp:
        worktree = Path(tmp) / "base"
        subprocess.run(["git", "worktree", "add", "--detach", str(worktree), args.base], cwd=ROOT, check=True, capture_output=True)
        if not args.files:
            new = [f for f in files if not (worktree / os.path.relpath(f, ROOT)).exists()]
            for f in new:
                print(f"НОВЫЙ {os.path.relpath(f, ROOT)} -- в {args.base} его нет, пропущен")
            files = [f for f in files if f not in new]
        try:
            print(f"Разбор {len(files)} файлов: {args.base} и текущий код…", flush=True)
            base = _run_dump(worktree, files, Path(tmp) / "base.json")
            head = _run_dump(ROOT, files, Path(tmp) / "head.json")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT, capture_output=True)

    changed = 0
    for path in files:
        a, b = base[path], head[path]
        name = os.path.relpath(path, ROOT)
        if a["error"] or b["error"]:
            if a["error"] != b["error"]:
                changed += 1
                print(f"ОШИБКА {name}: {a['error']} -> {b['error']}")
            continue
        sa, sb = _without_added_fields(a["scene"]), _without_added_fields(b["scene"])
        if sa != sb:
            changed += 1
            print(f"ИЗМЕНЕНО {name}")
            for line in _describe(sa, sb):
                print(f"    {line}")
    print(f"Совпало {len(files) - changed} из {len(files)}.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
