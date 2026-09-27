"""
Аутентификация по имени и паролю, без почты. Гостевой режим -- просто
отсутствие токена: разбор, генерация и правка открыты всем, токен нужен
только для сохранённых проектов.

Два токена:
* access (ACCESS_TOKEN_TTL_SECONDS, короткий) -- в заголовке каждого
  запроса, проверяется только по подписи и сроку, без обращения к Redis;
* refresh (REFRESH_TOKEN_TTL_SECONDS, долгий) -- только для получения нового
  access (/api/auth/refresh). Его сессия хранится в Redis, поэтому logout
  отзывает его сразу. Если Redis недоступен, сессии считаются активными
  (fail open).
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Optional

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from core.paths import ENV_FILE
from storage import cache

# Локально .env лежит в корне репозитория; JWT_SECRET нужен уже при импорте.
# load_dotenv без override не перетирает уже заданные переменные.
load_dotenv(ENV_FILE)

# Заглушка для разработки: с ней токен может подделать любой, кто видел
# исходники, поэтому в проде JWT_SECRET обязателен. Фиксированная, чтобы
# перезапуск с --reload не отзывал все токены.
_DEV_SECRET = "greencity-insecure-dev-secret-change-me"
JWT_SECRET = os.environ.get("JWT_SECRET") or _DEV_SECRET
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 15 * 60  # короткий -- его не проверяют по Redis, поэтому короткий срок и есть вся защита
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # долгий, но отзываемый через Redis (logout)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,32}$")
MIN_PASSWORD_LENGTH = 6


class AuthError(ValueError):
    """Некорректные учётные данные или токен при регистрации/входе/обновлении
    -- отдельно от HTTPException, чтобы бизнес-логика (projects.py) не
    зависела от FastAPI."""


def validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise AuthError("Имя пользователя: 3-32 символа, латиница/цифры/._")
    return username


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Пароль короче {MIN_PASSWORD_LENGTH} символов")
    return password


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # Битый/чужого формата хэш в базе -- не роняем запрос 500-й, просто
        # "неверный пароль", как и для обычного несовпадения.
        return False


def _encode(payload: dict) -> str:
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: str, username: str) -> str:
    payload = {"sub": user_id, "username": username, "type": "access", "exp": int(time.time()) + ACCESS_TOKEN_TTL_SECONDS}
    return _encode(payload)


def create_refresh_token(user_id: str, username: str) -> tuple[str, str]:
    """(токен, sid). sid возвращается отдельно, чтобы вызывающий код
    (projects.py) сохранил сессию в Redis, не декодируя тут же свежесозданный
    токен ради одного поля."""
    sid = uuid.uuid4().hex
    payload = {
        "sub": user_id,
        "username": username,
        "sid": sid,
        "type": "refresh",
        "exp": int(time.time()) + REFRESH_TOKEN_TTL_SECONDS,
    }
    return _encode(payload), sid


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


async def refresh_access_token(refresh_token: str) -> tuple[str, str]:
    """(новый access-токен, username) по действительному refresh-токену, или
    поднимает AuthError. Токен без активной сессии в Redis (отозван через
    logout) новый access-токен не получит, даже если подпись и срок самого
    JWT ещё в порядке -- в этом и смысл разделения на access/refresh."""
    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise AuthError("Недействительный refresh-токен, войдите заново")
    sid = payload.get("sid")
    if sid and not await cache.session_is_active(sid, payload["sub"]):
        raise AuthError("Сессия завершена, войдите заново")
    return create_access_token(payload["sub"], payload["username"]), payload["username"]


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip()


class CurrentUser:
    """Данные пользователя из access-токена -- без похода в базу или в Redis
    на каждый запрос: username/id в самом токене, а его короткий срок жизни
    и есть защита от того, что токен вообще нельзя отозвать напрямую (для
    этого и нужен отдельный, отзываемый refresh-токен)."""

    def __init__(self, user_id: str, username: str):
        self.id = user_id
        self.username = username


# Схема для Swagger: кнопка Authorize, значение -- "Bearer <access_token>".
_authorization_header = APIKeyHeader(
    name="Authorization",
    scheme_name="Bearer",
    description="Введите: Bearer <access_token> (токен из /api/auth/login).",
    auto_error=False,
)


async def require_user(authorization: Optional[str] = Security(_authorization_header)) -> CurrentUser:
    """Зависимость для эндпоинтов проектов: нужен действующий access-токен.
    async -- проверка JWT мгновенная, и ей незачем ждать свободный поток
    из пула, занятого тяжёлой геометрией."""
    token = _bearer_token(authorization)
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        raise HTTPException(401, "Требуется вход в аккаунт")
    return CurrentUser(payload["sub"], payload["username"])
