"""
Регистрация, вход и хранение проектов в MongoDB (storage/db.py). Не больше
MAX_PROJECTS_PER_USER проектов на аккаунт -- проверяется при создании.

bcrypt -- единственная блокирующая часть, поэтому он выполняется в отдельном
потоке (asyncio.to_thread), чтобы не подвешивать event loop.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from accounts import project_files
from accounts.auth import (
    REFRESH_TOKEN_TTL_SECONDS,
    AuthError,
    create_access_token,
    create_refresh_token,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from accounts.schemas import AuthResponse, Project, ProjectFile, ProjectSummary, SharedProject
from core.schemas import Scene
from storage import cache
from storage.db import projects as projects_col
from storage.db import users as users_col

MAX_PROJECTS_PER_USER = 3


class ProjectsError(ValueError):
    """Ожидаемая (не 500-я) ошибка бизнес-логики: занятое имя, лимит
    проектов, неверный пароль, чужой/несуществующий проект."""


class NotFoundError(ProjectsError):
    """Отдельно от остальных ProjectsError -- main.py превращает её в 404,
    а не в 400: "такого проекта нет или он не ваш" -- не ошибка запроса."""


def _now() -> datetime:
    return datetime.now(UTC)


async def _issue_tokens(user_id: str, username: str) -> AuthResponse:
    """Access + refresh для нового или вошедшего пользователя -- одно и то
    же в register() и login(), поэтому вынесено сюда. Сессия refresh-токена
    кладётся в Redis (cache.py) сразу же -- иначе logout не смог бы её
    отозвать до первого удачного истечения."""
    access = create_access_token(user_id, username)
    refresh, sid = create_refresh_token(user_id, username)
    await cache.store_session(sid, user_id, REFRESH_TOKEN_TTL_SECONDS)
    return AuthResponse(access_token=access, refresh_token=refresh, username=username)


# --- Аккаунты -----------------------------------------------------------------


async def register(username: str, password: str) -> AuthResponse:
    try:
        username = validate_username(username)
        password = validate_password(password)
    except AuthError as e:
        raise ProjectsError(str(e)) from e

    password_hash = await asyncio.to_thread(hash_password, password)
    doc = {"username": username, "password_hash": password_hash, "created_at": _now()}
    try:
        result = await users_col.insert_one(doc)
    except DuplicateKeyError as e:
        raise ProjectsError(f"Имя пользователя «{username}» уже занято") from e
    return await _issue_tokens(str(result.inserted_id), username)


async def login(username: str, password: str) -> AuthResponse:
    user = await users_col.find_one({"username": username.strip()})
    if user is None or not await asyncio.to_thread(verify_password, password, user["password_hash"]):
        raise ProjectsError("Неверное имя пользователя или пароль")
    return await _issue_tokens(str(user["_id"]), user["username"])


# --- Проекты --------------------------------------------------------------------


def _owner_filter(user_id: str, project_id: str) -> dict:
    try:
        return {"_id": ObjectId(project_id), "owner_id": user_id}
    except InvalidId as e:
        raise NotFoundError("Такого проекта нет") from e


def _to_summary(doc: dict) -> ProjectSummary:
    return ProjectSummary(
        id=str(doc["_id"]),
        name=doc["name"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        files_count=len(doc.get("files") or []),
        shared=bool(doc.get("share_token")),
    )


def _files(doc: dict) -> list[ProjectFile]:
    return [ProjectFile.model_validate(f) for f in doc.get("files") or []]


def _to_project(doc: dict) -> Project:
    return Project(
        id=str(doc["_id"]),
        name=doc["name"],
        scene=Scene.model_validate(doc["scene"]),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        files=_files(doc),
        share_token=doc.get("share_token"),
    )


async def list_projects(user_id: str) -> list[ProjectSummary]:
    cursor = projects_col.find({"owner_id": user_id}, {"scene": 0}).sort("updated_at", -1)
    return [_to_summary(doc) async for doc in cursor]


async def create_project(user_id: str, name: str, scene: Scene) -> Project:
    if await projects_col.count_documents({"owner_id": user_id}) >= MAX_PROJECTS_PER_USER:
        raise ProjectsError(
            f"Достигнут лимит {MAX_PROJECTS_PER_USER} проектов. Удалите один из существующих, чтобы сохранить новый."
        )
    now = _now()
    doc = {
        "owner_id": user_id,
        "name": name.strip(),
        "scene": scene.model_dump(mode="json"),
        "created_at": now,
        "updated_at": now,
    }
    result = await projects_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_project(doc)


async def get_project(user_id: str, project_id: str) -> Project:
    doc = await projects_col.find_one(_owner_filter(user_id, project_id))
    if doc is None:
        raise NotFoundError("Такого проекта нет или он не ваш")
    return _to_project(doc)


async def update_project(user_id: str, project_id: str, name: Optional[str], scene: Optional[Scene]) -> Project:
    update: dict = {"updated_at": _now()}
    if name is not None:
        update["name"] = name.strip()
    if scene is not None:
        update["scene"] = scene.model_dump(mode="json")
    doc = await projects_col.find_one_and_update(
        _owner_filter(user_id, project_id),
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise NotFoundError("Такого проекта нет или он не ваш")
    return _to_project(doc)


async def delete_project(user_id: str, project_id: str) -> None:
    result = await projects_col.delete_one(_owner_filter(user_id, project_id))
    if result.deleted_count == 0:
        raise NotFoundError("Такого проекта нет или он не ваш")
    await asyncio.to_thread(project_files.remove_project, project_id)


# --- Файлы проекта ----------------------------------------------------------------


def _file_of(doc: dict, file_id: str) -> tuple[ProjectFile, Path]:
    for meta in _files(doc):
        if meta.id == file_id:
            return meta, project_files.path_of(str(doc["_id"]), meta.id)
    raise NotFoundError("Такого файла в проекте нет")


async def get_file(user_id: str, project_id: str, file_id: str) -> tuple[ProjectFile, Path]:
    doc = await projects_col.find_one(_owner_filter(user_id, project_id), {"files": 1})
    if doc is None:
        raise NotFoundError("Такого проекта нет или он не ваш")
    return _file_of(doc, file_id)


async def delete_file(user_id: str, project_id: str, file_id: str) -> None:
    meta, _path = await get_file(user_id, project_id, file_id)
    await projects_col.update_one(_owner_filter(user_id, project_id), {"$pull": {"files": {"id": meta.id}}})
    await asyncio.to_thread(project_files.remove, project_id, [meta.id])


async def replace_auto_files(user_id: str, project_id: str, files: list[ProjectFile]) -> list[ProjectFile]:
    """Новый комплект файлов после GreenPlan вместо прошлого (auto); файлы,
    добавленные не комплектом, остаются. Возвращает все файлы проекта."""
    doc = await projects_col.find_one(_owner_filter(user_id, project_id), {"files": 1})
    if doc is None:
        await asyncio.to_thread(project_files.remove, project_id, [f.id for f in files])
        raise NotFoundError("Такого проекта нет или он не ваш")
    old = _files(doc)
    kept = [f for f in old if not f.auto]
    all_files = kept + files
    await projects_col.update_one(
        _owner_filter(user_id, project_id),
        {"$set": {"files": [f.model_dump(mode="python") for f in all_files], "updated_at": _now()}},
    )
    await asyncio.to_thread(project_files.remove, project_id, [f.id for f in old if f.auto])
    return all_files


# --- Доступ по ссылке ---------------------------------------------------------------


async def set_sharing(user_id: str, project_id: str, enabled: bool) -> Optional[str]:
    """Включить доступ по ссылке (ключ сохраняется, если уже был) или выключить:
    выключение отзывает ключ навсегда, новое включение даёт новую ссылку."""
    doc = await projects_col.find_one(_owner_filter(user_id, project_id), {"share_token": 1})
    if doc is None:
        raise NotFoundError("Такого проекта нет или он не ваш")
    if not enabled:
        await projects_col.update_one(_owner_filter(user_id, project_id), {"$unset": {"share_token": ""}})
        return None
    token = doc.get("share_token") or secrets.token_urlsafe(16)
    await projects_col.update_one(_owner_filter(user_id, project_id), {"$set": {"share_token": token}})
    return token


async def _shared_doc(token: str, projection: Optional[dict] = None) -> dict:
    doc = await projects_col.find_one({"share_token": token}, projection) if token else None
    if doc is None:
        raise NotFoundError("Ссылка недействительна: проект удалён или доступ по ссылке выключен")
    return doc


async def get_shared(token: str) -> SharedProject:
    doc = await _shared_doc(token)
    return SharedProject(name=doc["name"], scene=Scene.model_validate(doc["scene"]), updated_at=doc["updated_at"], files=_files(doc))


async def get_shared_file(token: str, file_id: str) -> tuple[ProjectFile, Path]:
    return _file_of(await _shared_doc(token, {"files": 1}), file_id)
