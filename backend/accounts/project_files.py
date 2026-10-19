"""
Файлы проекта на диске: документы и картинки по результату GreenPlan
(записка, объяснения, выгрузка DXF и её проверка, визуализации). Каталог --
GREENCITY_PROJECT_FILES_DIR (в Docker -- том), по умолчанию var/project_files;
файлы проекта -- в подкаталоге с его id, имя файла -- id записи. Описание
файлов (имя для сохранения, тип, размер) -- в документе проекта в MongoDB
(accounts/projects.py).
"""

from __future__ import annotations

import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from accounts.schemas import ProjectFile
from core.paths import REPO_ROOT


def files_dir() -> Path:
    return Path(os.environ.get("GREENCITY_PROJECT_FILES_DIR") or REPO_ROOT / "var" / "project_files")


def _project_dir(project_id: str) -> Path:
    # id проекта -- ObjectId (24 hex), id файла -- token_hex: путь не выходит из каталога.
    if not project_id.isalnum():
        raise ValueError("неверный id проекта")
    return files_dir() / project_id


def path_of(project_id: str, file_id: str) -> Path:
    if not file_id.isalnum():
        raise ValueError("неверный id файла")
    return _project_dir(project_id) / file_id


def store(project_id: str, name: str, kind: str, media_type: str, content: bytes, auto: bool = True) -> ProjectFile:
    """Записать файл (атомарно: во временный и переименовать) и вернуть его описание."""
    file_id = secrets.token_hex(8)
    target = path_of(project_id, file_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.replace(target)
    return ProjectFile(
        id=file_id, name=name, kind=kind, media_type=media_type, size=len(content), created_at=datetime.now(UTC), auto=auto
    )


def remove(project_id: str, file_ids: list[str]) -> None:
    for file_id in file_ids:
        path_of(project_id, file_id).unlink(missing_ok=True)


def remove_project(project_id: str) -> None:
    shutil.rmtree(_project_dir(project_id), ignore_errors=True)
