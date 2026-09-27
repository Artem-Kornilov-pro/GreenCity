"""
Хранилище исходных чертежей -- ради экспорта ПОВЕРХ исходника (ТЗ: "исходные
слои подосновы, коммуникаций и городской среды не изменяются и не
перезаписываются"; результат -- отдельными слоями). Сцена в редакторе -- уже
разобранная модель: в ней нет подписей, штриховок, блоков и слоёв, которые
парсер не читает, а координаты сдвинуты к центру участка. Собрать из неё тот
же чертёж нельзя -- поэтому сам исходник хранится здесь, а в сцене остаётся
только его id (meta.sourceId), и экспорт дописывает слои результата в копию
исходника (exchange/dxf_overlay.py).

Хранение по содержимому (sha256): один и тот же файл, загруженный дважды или
разными пользователями, лежит один раз. DXF -- одним файлом <id>.dxf, пачка
DWG -- каталогом <id>/ с самими .dwg: при экспорте она заново конвертируется
тем же конвейером, что и при загрузке (exchange/dwg_job.py), -- так загрузка
не платит за сохранение собранного чертежа (на крупной пачке это +14 с), а
платит только редкий экспорт.

Каталог -- GREENCITY_SOURCES_DIR (в Docker -- том, чтобы исходники
переживали перезапуск), по умолчанию var/sources в корне репозитория.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from core.paths import REPO_ROOT

log = logging.getLogger("greencity.parse")

_ID = re.compile(r"^[0-9a-f]{32}$")


def sources_dir() -> Path:
    return Path(os.environ.get("GREENCITY_SOURCES_DIR") or REPO_ROOT / "var" / "sources")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_dxf(data: bytes) -> Optional[str]:
    """id исходного DXF или None, если сохранить не удалось (диск, права):
    загрузка при этом не падает -- экспорт просто соберёт чертёж из сцены."""
    source_id = hashlib.sha256(data).hexdigest()[:32]
    path = sources_dir() / f"{source_id}.dxf"
    try:
        if not path.exists():
            _atomic_write(path, data)
    except OSError as e:
        log.warning("исходный DXF не сохранён: %s", e)
        return None
    return source_id


def save_dwg_batch(paths: list[Path]) -> Optional[str]:
    """id пачки DWG: сами файлы, в порядке загрузки (от него зависит сборка)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    source_id = digest.hexdigest()[:32]
    folder = sources_dir() / source_id
    try:
        if not (folder / "order.txt").exists():
            for index, path in enumerate(paths):
                _atomic_write(folder / f"{index:03d}_{path.name}", path.read_bytes())
            _atomic_write(folder / "order.txt", "\n".join(f"{i:03d}_{p.name}" for i, p in enumerate(paths)).encode("utf-8"))
    except OSError as e:
        log.warning("исходная пачка DWG не сохранена: %s", e)
        return None
    return source_id


def find(source_id: Optional[str]) -> Optional[tuple[str, Path | list[Path]]]:
    """("dxf", путь) или ("dwg", [пути в порядке загрузки]) -- или None, если
    id нет, он не наш (защита от путей вида ../) или исходник уже удалён."""
    if not source_id or not _ID.match(source_id):
        return None
    root = sources_dir()
    dxf = root / f"{source_id}.dxf"
    if dxf.is_file():
        return "dxf", dxf
    order = root / source_id / "order.txt"
    if order.is_file():
        names = [line for line in order.read_text(encoding="utf-8").splitlines() if line]
        paths = [root / source_id / name for name in names]
        if all(p.is_file() for p in paths):
            return "dwg", paths
    return None
