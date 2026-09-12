"""
Аутентификация: логин/пароль, без подтверждения почты -- по требованию
задачи ("не надо делать верификацию почты и остальное просто логин пароль").
Гостевой режим (тоже по требованию, "для тестов") не описан здесь вообще:
это просто отсутствие токена -- эндпоинты DXF/генерации/правки текстом
как были, так и остались полностью открытыми (main.py их не трогает), и
только эндпоинты проектов (projects.py) требуют токен. Гость может пользоваться
редактором как раньше, только не может сохранять именованные проекты.

Access + refresh, а не один долгоживущий токен:
* access-токен (ACCESS_TOKEN_TTL_SECONDS, короткий) идёт в заголовке каждого
  запроса. Проверяется ЧИСТО по подписи и сроку (require_user) -- никакого
  похода в Redis на каждый запрос, поэтому его компрометация опасна лишь на
  короткое окно, и это вся его защита;
* refresh-токен (REFRESH_TOKEN_TTL_SECONDS, долгий) нужен только чтобы
  получить новый access-токен (/api/auth/refresh) и не спрашивать пароль
  заново каждые 15 минут. Его сессия хранится в Redis (cache.py:
  store_session/session_is_active/revoke_session) -- это и даёт настоящий
  отзыв: logout удаляет сессию, и refresh-токен сразу перестаёт работать,
  даже если сам JWT ещё валиден по подписи и сроку. Если Redis недоступен --
  сессии считаются активными (fail open, см. cache.py): доступность важнее,
  чем то, что отзыв временно не сработает, пока Redis не поднимут.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import bcrypt
import cache
import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException

# Локально .env лежит в корне репозитория, читает его не только llm_editor.py
# -- JWT_SECRET нужен здесь же, при импорте модуля (см. ниже). В Docker
# переменные уже приходят из env_file, а load_dotenv без override
# существующие значения не перетирает, так что повторный вызов из другого
# модуля (llm_editor.py) безвреден.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Дев-заглушка -- ЛЮБОЙ, кто прочитает исходники, сможет подделать токен.
# Годится для хакатон-прототипа за закрытым портом; при реальном общем доступе
# JWT_SECRET обязателен в .env. Фиксированная (не случайная при каждом
# запуске) заглушка -- намеренно: со случайной каждый `uvicorn --reload`
# отзывал бы все выданные токены, и в разработке пришлось бы логиниться заново
# после любой правки кода.
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


def require_user(authorization: Optional[str] = Header(default=None)) -> CurrentUser:
    """FastAPI-зависимость для эндпоинтов, которым нужен вошедший
    пользователь (проекты). Остальные эндпоинты (DXF, генерация, правка
    текстом) эту зависимость не используют -- гостевой режим не требует
    отдельного флага, он просто не ходит в эндпоинты с этой зависимостью."""
    token = _bearer_token(authorization)
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        raise HTTPException(401, "Требуется вход в аккаунт")
    return CurrentUser(payload["sub"], payload["username"])
