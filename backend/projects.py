"""
Регистрация/вход и хранение проектов в MongoDB (db.py). Бизнес-логика
отдельно от main.py (эндпоинты там -- тонкая обвязка, ошибки этого модуля
превращаются в HTTP-коды централизованно, как и с LlmError в llm_editor.py).

Каждый пользователь может хранить не больше MAX_PROJECTS_PER_USER проектов
(по условию задачи) -- проверяется при создании нового, не при сохранении
существующего.

Асинхронно (db.py -- AsyncMongoClient): единственная блокирующая часть --
bcrypt (auth.hash_password/verify_password), намеренно медленный CPU-занятый
алгоритм. Он не async сам по себе, поэтому запускается в отдельном потоке
(asyncio.to_thread) -- иначе хэширование пароля одного запроса подвесило бы
event loop и все остальные запросы на время работы bcrypt.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Optional

import cache
from auth import (
    REFRESH_TOKEN_TTL_SECONDS,
    AuthError,
    create_access_token,
    create_refresh_token,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from bson import ObjectId
from bson.errors import InvalidId
from db import projects as projects_col
from db import users as users_col
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from schemas import Scene
from schemas_auth import AuthResponse, Project, ProjectSummary

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
    return ProjectSummary(id=str(doc["_id"]), name=doc["name"], created_at=doc["created_at"], updated_at=doc["updated_at"])


def _to_project(doc: dict) -> Project:
    return Project(
        id=str(doc["_id"]),
        name=doc["name"],
        scene=Scene.model_validate(doc["scene"]),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


async def list_projects(user_id: str) -> list[ProjectSummary]:
    cursor = projects_col.find({"owner_id": user_id}).sort("updated_at", -1)
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
