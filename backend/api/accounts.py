"""
Эндпоинты аккаунтов и проектов: регистрация и вход по имени и паролю,
обновление и отзыв токена, сохранённые проекты пользователя в MongoDB.
Без токена работает всё, кроме проектов (гостевой режим).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from accounts import projects as projects_service
from accounts.auth import AuthError, CurrentUser, decode_token, refresh_access_token, require_user
from accounts.projects import NotFoundError, ProjectsError
from accounts.schemas import (
    AccessTokenResponse,
    AuthResponse,
    LoginRequest,
    MeResponse,
    Project,
    ProjectCreate,
    ProjectSummary,
    ProjectUpdate,
    RefreshRequest,
    RegisterRequest,
)
from monitoring import metrics
from storage import cache

router = APIRouter()
_auth_logger = logging.getLogger("greencity.auth")

# Ограничение частоты по IP -- защита от подбора пароля и массовой регистрации.
REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S = 5, 3600
LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S = 10, 300

AUTH_ERRORS = {401: {"description": "Нет входа или токен недействителен."}, 503: {"description": "Хранилище недоступно."}}


class StatusOk(BaseModel):
    status: str = "ok"


def _mongo_unavailable(e: PyMongoError) -> HTTPException:
    logging.getLogger("greencity.db").warning("MongoDB недоступна на запросе: %s", e)
    return HTTPException(503, "Хранилище аккаунтов и проектов сейчас недоступно, попробуйте позже")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post(
    "/api/auth/register",
    response_model=AuthResponse,
    tags=["Аккаунты"],
    summary="Регистрация",
    responses={400: {"description": "Имя занято или пароль не подходит."}, 429: {"description": "Слишком много регистраций."}},
)
async def register(request: RegisterRequest, http_request: Request):
    """Только имя пользователя и пароль, без почты. Сразу возвращает
    access- и refresh-токен."""
    client_ip = _client_ip(http_request)
    if not await cache.check_rate_limit(f"rl:register:{client_ip}", REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S):
        metrics.auth_registrations_total.labels(outcome="rate_limited").inc()
        metrics.rate_limit_blocks_total.labels(endpoint="register").inc()
        _auth_logger.warning("регистрация отклонена лимитом частоты", extra={"client_ip": client_ip})
        raise HTTPException(429, "Слишком много регистраций с этого адреса, попробуйте позже")
    try:
        result = await projects_service.register(request.username, request.password)
    except ProjectsError as e:
        metrics.auth_registrations_total.labels(outcome="rejected").inc()
        _auth_logger.info("регистрация отклонена: %s", e, extra={"client_ip": client_ip, "username": request.username})
        raise HTTPException(400, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    metrics.auth_registrations_total.labels(outcome="success").inc()
    _auth_logger.info("зарегистрирован новый пользователь", extra={"client_ip": client_ip, "username": request.username})
    return result


@router.post(
    "/api/auth/login",
    response_model=AuthResponse,
    tags=["Аккаунты"],
    summary="Вход",
    responses={401: {"description": "Неверное имя или пароль."}, 429: {"description": "Слишком много попыток."}},
)
async def login(request: LoginRequest, http_request: Request):
    """Возвращает access-токен (для заголовка Authorization) и refresh-токен."""
    client_ip = _client_ip(http_request)
    rate_key = f"rl:login:{client_ip}"
    if not await cache.check_rate_limit(rate_key, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S):
        metrics.auth_logins_total.labels(outcome="rate_limited").inc()
        metrics.rate_limit_blocks_total.labels(endpoint="login").inc()
        _auth_logger.warning("вход отклонён лимитом частоты", extra={"client_ip": client_ip})
        raise HTTPException(429, "Слишком много попыток входа с этого адреса, попробуйте позже")
    try:
        result = await projects_service.login(request.username, request.password)
    except ProjectsError as e:
        metrics.auth_logins_total.labels(outcome="rejected").inc()
        _auth_logger.info("вход отклонён: %s", e, extra={"client_ip": client_ip, "username": request.username})
        raise HTTPException(401, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    await cache.reset_rate_limit(rate_key)
    metrics.auth_logins_total.labels(outcome="success").inc()
    _auth_logger.info("успешный вход", extra={"client_ip": client_ip, "username": request.username})
    return result


@router.post(
    "/api/auth/refresh",
    response_model=AccessTokenResponse,
    tags=["Аккаунты"],
    summary="Обновить access-токен",
    responses={401: {"description": "Refresh-токен отозван или недействителен."}},
)
async def refresh(request: RefreshRequest):
    """Новый access-токен по refresh-токену без повторного ввода пароля."""
    try:
        access_token, username = await refresh_access_token(request.refresh_token)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return AccessTokenResponse(access_token=access_token, username=username)


@router.post("/api/auth/logout", response_model=StatusOk, tags=["Аккаунты"], summary="Выход")
async def logout(request: RefreshRequest):
    """Отзывает refresh-токен сразу, не дожидаясь срока его действия."""
    payload = decode_token(request.refresh_token)
    sid = payload.get("sid") if payload else None
    if sid:
        await cache.revoke_session(sid)
        _auth_logger.info("выход, сессия отозвана", extra={"username": payload.get("username")})
    return StatusOk()


@router.get("/api/auth/me", response_model=MeResponse, tags=["Аккаунты"], summary="Текущий пользователь", responses=AUTH_ERRORS)
async def whoami(user: CurrentUser = Depends(require_user)):
    """Проверяет access-токен и возвращает имя пользователя."""
    return MeResponse(username=user.username)


@router.get("/api/projects", response_model=list[ProjectSummary], tags=["Проекты"], summary="Список проектов", responses=AUTH_ERRORS)
async def list_projects(user: CurrentUser = Depends(require_user)):
    """Проекты пользователя без сцены -- для выбора, какой открыть."""
    try:
        return await projects_service.list_projects(user.id)
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.post(
    "/api/projects",
    response_model=Project,
    tags=["Проекты"],
    summary="Сохранить новый проект",
    responses={**AUTH_ERRORS, 400: {"description": "Лимит проектов на аккаунт исчерпан."}},
)
async def create_project(request: ProjectCreate, user: CurrentUser = Depends(require_user)):
    """Сохраняет сцену как новый проект. Не больше трёх проектов на аккаунт."""
    try:
        return await projects_service.create_project(user.id, request.name, request.scene)
    except ProjectsError as e:
        raise HTTPException(400, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.get(
    "/api/projects/{project_id}",
    response_model=Project,
    tags=["Проекты"],
    summary="Открыть проект",
    responses={**AUTH_ERRORS, 404: {"description": "Проект не найден."}},
)
async def get_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        return await projects_service.get_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.put(
    "/api/projects/{project_id}",
    response_model=Project,
    tags=["Проекты"],
    summary="Обновить проект",
    responses={**AUTH_ERRORS, 404: {"description": "Проект не найден."}},
)
async def update_project(project_id: str, request: ProjectUpdate, user: CurrentUser = Depends(require_user)):
    """Переименовать проект, перезаписать сцену или и то, и другое."""
    try:
        return await projects_service.update_project(user.id, project_id, request.name, request.scene)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.delete(
    "/api/projects/{project_id}",
    response_model=StatusOk,
    tags=["Проекты"],
    summary="Удалить проект",
    responses={**AUTH_ERRORS, 404: {"description": "Проект не найден."}},
)
async def delete_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        await projects_service.delete_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    return StatusOk()
