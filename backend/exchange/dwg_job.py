"""
Разбор пачки .dwg для /api/parse-dwg -- в отдельном короткоживущем процессе
и не больше DWG_MAX_PARALLEL пачек одновременно на весь backend.

Зачем отдельный процесс, а не прямо в обработчике запроса. Реальная пачка
(5 файлов, 47 МБ DWG, ~200 тыс. объектов) держит в пике ~1,8 ГБ: ezdxf хранит
весь чертёж питоновскими объектами. После запроса процесс uvicorn эту память
системе не возвращает (аллокатор CPython), и рабочий процесс так и живёт с
1,5 ГБ. В проде (docker-compose.prod.yml: 3 процесса, лимит контейнера 3 ГБ)
две-три одновременные загрузки упирались в лимит, ядро убивало процесс
посреди запроса, пользователь получал 502. Дочерний процесс отдаёт всю память
системе, как только завершается, а падение в нём (нехватка памяти) не роняет
рабочий процесс uvicorn -- эндпоинт отвечает понятной ошибкой вместо 502.

Параллельность. Внутри пачки файлы разбираются в DWG_WORKERS отдельных
процессах (exchange/dwg_batch_converter.py): каждый держит свой файл, ~0,5 ГБ,
главный процесс пачки -- ~0,75 ГБ на склейку и разбор (было 1,6 ГБ на всё в
одном процессе). Реальная пачка из 5 файлов -- 18,5 с вместо 28,5. Одновременно
разбираются до DWG_MAX_PARALLEL пачек с разных аккаунтов: на сервере 4 vCPU /
8 ГБ (docker-compose.prod.yml, лимит бэкенда 4 ГБ) -- 2 пачки по 3 процесса,
в худшем случае ~3 ГБ. Слоты -- файловые блокировки (fcntl.flock) в общем /tmp
контейнера, поэтому ограничение действует на все процессы uvicorn сразу, а не
на каждый по отдельности. Сверх лимита загрузки ждут своей очереди, а не
отнимают память друг у друга.

Сцена пишется дочерним процессом прямо в JSON-файл, и обработчик отдаёт эти
байты как есть: 30 МБ JSON не превращаются в питоновский словарь в рабочем
процессе uvicorn ни разу.

    python -m exchange.dwg_job <каталог с .dwg> <scene.json> <meta.json> <файл.dwg>...
"""

from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from core.paths import BACKEND_DIR

log = logging.getLogger("greencity.parse")

# Сколько пачек DWG разбирается одновременно на весь backend (DWG_MAX_PARALLEL)
# и сколько процессов разбирает файлы одной пачки (DWG_WORKERS). Пачка -- до
# ~0,75 ГБ в главном процессе плюс ~0,5 ГБ на каждый рабочий: 2 x 3 укладываются
# в лимит бэкенда 4 ГБ (docker-compose.prod.yml) вместе с процессами uvicorn.
DEFAULT_MAX_PARALLEL = 2
DEFAULT_WORKERS = 3
# Сколько ждать свободного слота, прежде чем ответить "сервер занят".
SLOT_WAIT_S = 600
# Потолок на разбор одной пачки: зависший dwg2dxf/ezdxf не должен держать
# слот вечно.
JOB_TIMEOUT_S = 900
# Каталог файлов-слотов: общий /tmp контейнера -- общий для всех процессов
# uvicorn. DWG_SLOT_DIR -- чтобы тесты не делили слоты между собой.
_DEFAULT_SLOT_DIR = Path(tempfile.gettempdir()) / "greencity-dwg-slots"


class DwgJobError(Exception):
    """Ошибка, которую эндпоинт отдаёт клиенту как есть: HTTP-код и текст.
    outcome и failed -- для метрик (greencity_dwg_batch_conversions_total)."""

    def __init__(self, status: int, detail: str, outcome: str, failed: int = 0):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.outcome = outcome
        self.failed = failed


def build_scene(dwg_paths: list[Path], work_dir: Path, source_id: Optional[str] = None) -> tuple[dict, dict]:
    """(сцена, сводка для метрик и логов). Бросает DwgJobError."""
    from core.building_setbacks import compute_building_setbacks
    from exchange import dwg_batch_converter
    from exchange.dxf_parser import parse_dxf_doc

    try:
        workers = max(1, int(os.environ.get("DWG_WORKERS", DEFAULT_WORKERS)))
        result = dwg_batch_converter.merge_dwg_files(dwg_paths, work_dir, workers=workers)
    except dwg_batch_converter.Dwg2DxfNotFound as e:
        raise DwgJobError(503, str(e), "tool_missing") from e

    if not result.converted:
        detail = "; ".join(f"{name}: {err}" for name, err in result.failed.items())
        raise DwgJobError(400, f"Не удалось сконвертировать ни один .dwg-файл. {detail}", "all_failed", len(result.failed))

    # Разбор собранного документа прямо из памяти. Раньше он записывался в
    # combined.dxf и тут же читался заново через parse_dxf_file: на пачке в
    # 47 МБ DWG это +14 с на запись, ~+30 с на повторное чтение и вторая
    # копия чертежа в памяти (пик 2,1 ГБ против 1,75). Результат -- тот же
    # до байта (сверено на реальной пачке).
    try:
        scene = parse_dxf_doc(result.doc)
    except Exception as e:
        log.warning("не удалось разобрать смёрженный DWG-батч: %s", e)
        raise DwgJobError(400, f"Не удалось разобрать результат конвертации: {e}", "parse_error") from e
    del result.doc
    gc.collect()

    scene["buildingSetbacks"] = compute_building_setbacks(scene.get("objects", []))
    if source_id:
        scene["meta"]["sourceId"] = source_id
    if result.failed:
        scene["dwgConversionWarnings"] = [{"file": name, "error": err} for name, err in result.failed.items()]
    summary = {
        "converted": len(result.converted),
        "failed": len(result.failed),
        "objects": len(scene.get("objects", [])),
        "zones": len(scene.get("restrictions", [])),
    }
    return scene, summary


def run(
    dwg_paths: list[Path], work_dir: Path, request_id: str = "-", isolated: Optional[bool] = None, source_id: Optional[str] = None
) -> tuple[bytes, dict]:
    """(JSON сцены, сводка). isolated=None -- по DWG_JOB_ISOLATED (по
    умолчанию да). В самом процессе -- для тестов, которые подменяют
    merge_dwg_files: подмена в дочерний процесс не переходит."""
    if isolated is None:
        isolated = os.environ.get("DWG_JOB_ISOLATED", "1") != "0"
    if not isolated:
        scene, summary = build_scene(dwg_paths, work_dir, source_id)
        return json.dumps(scene, ensure_ascii=False).encode("utf-8"), summary

    scene_path = work_dir / "scene.json"
    meta_path = work_dir / "meta.json"
    env = {**os.environ, "GREENCITY_REQUEST_ID": request_id, "GREENCITY_SOURCE_ID": source_id or ""}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "exchange.dwg_job", str(work_dir), str(scene_path), str(meta_path), *map(str, dwg_paths)],
            cwd=BACKEND_DIR,
            env=env,
            timeout=JOB_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise DwgJobError(504, f"Разбор DWG не уложился в {JOB_TIMEOUT_S // 60} минут", "timeout") from e

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if meta is None or completed.returncode != 0:
        # -9 -- процесс убит системой, почти всегда из-за нехватки памяти.
        reason = "не хватило памяти" if completed.returncode in (-9, 137) else f"код {completed.returncode}"
        raise DwgJobError(500, f"Разбор DWG прервался ({reason}). Попробуйте загрузить меньше файлов за раз.", "crashed")
    if not meta["ok"]:
        raise DwgJobError(meta["status"], meta["detail"], meta["outcome"], meta["failed"])
    return scene_path.read_bytes(), meta["summary"]


def build_export(dwg_paths: list[Path], work_dir: Path, scene_json: bytes) -> bytes:
    """DXF: пачка DWG, собранная так же, как при загрузке, и поверх неё --
    слои результата сцены (exchange/dxf_overlay.py)."""
    import io

    from core.schemas import Scene
    from exchange import dwg_batch_converter
    from exchange.dxf_overlay import overlay_scene

    workers = max(1, int(os.environ.get("DWG_WORKERS", DEFAULT_WORKERS)))
    result = dwg_batch_converter.merge_dwg_files(dwg_paths, work_dir, workers=workers)
    if not result.converted:
        raise DwgJobError(400, "Исходные DWG не сконвертировались при экспорте", "all_failed", len(result.failed))
    overlay_scene(Scene.model_validate_json(scene_json), result.doc)
    buf = io.StringIO()
    result.doc.write(buf)
    return buf.getvalue().encode("utf-8")


def run_export(
    dwg_paths: list[Path], work_dir: Path, scene, request_id: str = "-", isolated: Optional[bool] = None
) -> bytes:
    """DXF поверх исходной пачки DWG -- в отдельном процессе, как и разбор
    (та же память и те же причины, см. докстринг модуля)."""
    scene_json = scene.model_dump_json().encode("utf-8")
    if isolated is None:
        isolated = os.environ.get("DWG_JOB_ISOLATED", "1") != "0"
    if not isolated:
        return build_export(dwg_paths, work_dir, scene_json)
    scene_path, out_path, meta_path = work_dir / "scene.json", work_dir / "out.dxf", work_dir / "meta.json"
    scene_path.write_bytes(scene_json)
    env = {**os.environ, "GREENCITY_REQUEST_ID": request_id}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "exchange.dwg_job", "--export", str(work_dir), str(scene_path), str(out_path), str(meta_path),
             *map(str, dwg_paths)],
            cwd=BACKEND_DIR,
            env=env,
            timeout=JOB_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise DwgJobError(504, f"Экспорт DWG не уложился в {JOB_TIMEOUT_S // 60} минут", "timeout") from e
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if meta is None or completed.returncode != 0 or not meta["ok"]:
        raise DwgJobError(500, (meta or {}).get("detail") or f"Экспорт DWG прервался (код {completed.returncode})", "crashed")
    return out_path.read_bytes()


def _export_main(argv: list[str]) -> int:
    work_dir, scene_path, out_path, meta_path = (Path(a) for a in argv[:4])
    dwg_paths = [Path(a) for a in argv[4:]]
    try:
        out_path.write_bytes(build_export(dwg_paths, work_dir, scene_path.read_bytes()))
    except Exception as e:  # noqa: BLE001 -- причина уходит родителю в meta.json
        meta_path.write_text(json.dumps({"ok": False, "detail": str(e)}, ensure_ascii=False), encoding="utf-8")
        return 0
    meta_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return 0


@contextmanager
def slot(wait_s: float = SLOT_WAIT_S) -> Iterator[None]:
    """Один из DWG_MAX_PARALLEL слотов на весь backend (все процессы uvicorn
    одного контейнера). Ждёт свободного до wait_s секунд, иначе DwgJobError
    503. Без fcntl (Windows, локальные тесты) ограничения нет."""
    try:
        import fcntl
    except ImportError:
        yield
        return

    slots = max(1, int(os.environ.get("DWG_MAX_PARALLEL", DEFAULT_MAX_PARALLEL)))
    slot_dir = Path(os.environ.get("DWG_SLOT_DIR", _DEFAULT_SLOT_DIR))
    slot_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        for index in range(slots):
            with open(slot_dir / f"slot{index}.lock", "w") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                return
        if time.monotonic() >= deadline:
            raise DwgJobError(503, "Сервер занят разбором других DWG. Попробуйте через пару минут.", "busy")
        time.sleep(1.0)


def _main(argv: list[str]) -> int:
    from monitoring.logging_config import bind_request_id, configure_logging

    configure_logging()
    bind_request_id(os.environ.get("GREENCITY_REQUEST_ID", "-"))
    if argv and argv[0] == "--export":
        return _export_main(argv[1:])
    work_dir, scene_path, meta_path = (Path(a) for a in argv[:3])
    dwg_paths = [Path(a) for a in argv[3:]]
    try:
        scene, summary = build_scene(dwg_paths, work_dir, os.environ.get("GREENCITY_SOURCE_ID") or None)
    except DwgJobError as e:
        error = {"ok": False, "status": e.status, "detail": e.detail, "outcome": e.outcome, "failed": e.failed}
        meta_path.write_text(json.dumps(error, ensure_ascii=False), encoding="utf-8")
        return 0
    scene_path.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(json.dumps({"ok": True, "summary": summary}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
