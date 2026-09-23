"""
Эндпоинты аккаунтов и проектов: регистрация и вход по логину/паролю,
refresh/logout, сохранённые проекты пользователя (MongoDB). Гостевой режим --
это отсутствие токена: редактор и GreenPlan токена не требуют.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
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


# --- Аккаунты и проекты (auth.py, projects.py, db.py, cache.py) --------------
#
# Регистрация -- только логин/пароль, без подтверждения почты (по условию
# задачи). Гостевой режим -- это отсутствие этих эндпоинтов в обиходе:
# распарсить DXF, сгенерировать растительность и править текстом можно и без
# токена (эндпоинты выше его не требуют) -- только сохранить именованный
# проект нельзя, для этого и нужен аккаунт.
#
# Всё здесь асинхронно (AsyncMongoClient в db.py, redis.asyncio в cache.py) --
# сервис рассчитан на много одновременных пользователей, блокирующие клиенты
# заняли бы поток из ограниченного пула на каждое обращение к базе.
#
# Ошибки бизнес-логики (занятое имя, неверный пароль, лимит проектов, чужой
# проект, недействительный refresh-токен) -- ожидаемые, превращаются в
# понятный 400/401/404, а не 500; недоступность самой MongoDB -- в 503, как и
# недоступность LLM выше.


_auth_logger = logging.getLogger("greencity.auth")


def _mongo_unavailable(e: PyMongoError) -> HTTPException:
    logging.getLogger("greencity.db").warning("MongoDB недоступна на запросе: %s", e)
    return HTTPException(503, "Хранилище аккаунтов и проектов сейчас недоступно, попробуйте позже")


# Ограничение частоты (cache.py, Redis) -- по IP, отдельно на регистрацию и
# на вход: при большом числе пользователей это единственная защита от
# подбора пароля/массовой регистрации ботами, которая тут вообще есть, раз
# самого подтверждения почты по условию задачи нет. Недоступный Redis не
# блокирует вход -- см. cache.check_rate_limit.
REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S = 5, 3600  # 5 регистраций в час с одного адреса
LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S = 10, 300  # 10 попыток входа за 5 минут с одного адреса


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/api/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, http_request: Request):
    """Регистрация: только имя пользователя и пароль, без почты и её
    подтверждения. Сразу возвращает access- и refresh-токен -- отдельный
    вход после регистрации не нужен."""
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


@router.post("/api/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, http_request: Request):
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
    await cache.reset_rate_limit(rate_key)  # успешный вход -- не копить неудачные попытки на будущее
    metrics.auth_logins_total.labels(outcome="success").inc()
    _auth_logger.info("успешный вход", extra={"client_ip": client_ip, "username": request.username})
    return result


@router.post("/api/auth/refresh", response_model=AccessTokenResponse)
async def refresh(request: RefreshRequest):
    """Новый access-токен по refresh-токену -- фронтенд дёргает это сам,
    когда прежний access-токен истёк (см. frontend/src/auth.ts), не спрашивая
    пароль заново. Отозванный (logout) или битый refresh-токен -- 401."""
    try:
        access_token, username = await refresh_access_token(request.refresh_token)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return AccessTokenResponse(access_token=access_token, username=username)


@router.post("/api/auth/logout")
async def logout(request: RefreshRequest):
    """Отозвать refresh-токен немедленно (Redis: cache.revoke_session), не
    дожидаясь истечения REFRESH_TOKEN_TTL_SECONDS -- без этого выход был бы
    только локальным удалением токенов во фронтенде, а сам refresh-токен
    оставался бы действителен ещё до 30 дней. Не требует access-токена --
    предъявление самого refresh-токена и есть право его отозвать, как и в
    типичных эндпоинтах отзыва OAuth."""
    payload = decode_token(request.refresh_token)
    sid = payload.get("sid") if payload else None
    if sid:
        await cache.revoke_session(sid)
        _auth_logger.info("выход, сессия отозвана", extra={"username": payload.get("username")})
    return {"status": "ok"}


@router.get("/api/auth/me", response_model=MeResponse)
async def whoami(user: CurrentUser = Depends(require_user)):
    """Проверить access-токен и узнать, под кем он выдан -- фронтенд дёргает
    это при загрузке страницы, чтобы решить, показывать вход или уже
    авторизованный вид."""
    return MeResponse(username=user.username)


@router.get("/api/projects", response_model=list[ProjectSummary])
async def list_projects(user: CurrentUser = Depends(require_user)):
    try:
        return await projects_service.list_projects(user.id)
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.post("/api/projects", response_model=Project)
async def create_project(request: ProjectCreate, user: CurrentUser = Depends(require_user)):
    """Сохранить текущую сцену как новый проект. Не больше
    projects.MAX_PROJECTS_PER_USER на пользователя (по условию задачи) --
    лишний создать нельзя, нужно сперва удалить один из существующих."""
    try:
        return await projects_service.create_project(user.id, request.name, request.scene)
    except ProjectsError as e:
        raise HTTPException(400, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        return await projects_service.get_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.put("/api/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, request: ProjectUpdate, user: CurrentUser = Depends(require_user)):
    """Сохранить правки в уже существующий проект -- переименовать,
    перезаписать сцену, или и то, и другое; оба поля необязательны."""
    try:
        return await projects_service.update_project(user.id, project_id, request.name, request.scene)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        await projects_service.delete_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    return {"status": "ok"}
