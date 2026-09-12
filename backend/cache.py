"""
Redis -- для того, что законно можно потерять без вреда для правильности:
ограничение частоты попыток входа/регистрации (защита от подбора пароля,
важна именно потому, что сервис рассчитан на много пользователей), кэш
ответа /api/catalog (не меняется между запросами, но читается с диска и
собирается заново на каждый вызов -- под нагрузкой от многих пользователей
это лишняя работа на каждый) и реестр активных сессий по refresh-токенам
(auth.py) -- то, что делает logout настоящим отзывом, а не только удалением
токена во фронтенде.

Клиент асинхронный (redis.asyncio) -- по той же причине, что и AsyncMongoClient
в db.py: блокирующий клиент занимал бы поток из пула на каждое обращение к
Redis, а сервис должен обслуживать много пользователей одновременно.

Всё это -- намеренно "fail open": если Redis недоступен, вход/регистрация не
лимитируются, каталог считается напрямую, а refresh-токен считается активным
по одной лишь подписи JWT (см. session_is_active). Это тот же принцип
деградации, что и у MongoDB (db.py) и LLM (llm_editor.py) -- второстепенный
сервис лежит, а не весь бэкенд: доступность важнее для прототипа, чем то, что
при упавшем Redis отзыв сессии временно не сработает.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from redis.asyncio import Redis
from redis.exceptions import RedisError

# Локально .env лежит в корне репозитория -- REDIS_URL при желании задаётся
# там же (см. .env.example). load_dotenv без override уже выставленные
# переменные не перетирает, поэтому повторный вызов из других модулей
# (auth.py, db.py, llm_editor.py) безвреден.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("greencity.cache")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# decode_responses=True -- работаем со строками, а не с bytes, во всех
# вызывающих местах (main.py, здесь). socket_connect_timeout короткий по той
# же причине, что и serverSelectionTimeoutMS в db.py: недоступный Redis не
# должен подвешивать запрос на системный таймаут TCP (обычно десятки секунд).
_client = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1.0, socket_timeout=1.0)


async def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> bool:
    """True -- запрос можно выполнять, False -- превышен лимит попыток.
    Считает по фиксированному окну через INCR+EXPIRE: первый вызов на ключ
    ставит счётчик 1 и срок жизни window_seconds, следующие в том же окне
    только увеличивают счётчик. Недоступный Redis -- не повод блокировать
    вход всем подряд, поэтому при ошибке лимит считается не превышенным."""
    try:
        count = await _client.incr(key)
        if count == 1:
            await _client.expire(key, window_seconds)
        return count <= max_attempts
    except RedisError as e:
        logger.warning("Redis недоступен, ограничение частоты пропущено (%s): %s", key, e)
        return True


async def reset_rate_limit(key: str) -> None:
    """После успешного входа счётчик неудачных попыток можно сбросить сразу,
    не дожидаясь истечения окна -- иначе несколько опечаток подряд перед
    верным паролем без нужды приближали бы к лимиту следующую сессию."""
    with contextlib.suppress(RedisError):
        await _client.delete(key)  # необязательная оптимизация -- окно и так само истечёт


async def get_cached(key: str) -> Optional[str]:
    try:
        return await _client.get(key)
    except RedisError as e:
        logger.warning("Redis недоступен, чтение кэша пропущено (%s): %s", key, e)
        return None


async def set_cached(key: str, value: str, ttl_seconds: int) -> None:
    try:
        await _client.set(key, value, ex=ttl_seconds)
    except RedisError as e:
        logger.warning("Redis недоступен, запись в кэш пропущена (%s): %s", key, e)


# --- Сессии по refresh-токенам (auth.py) ------------------------------------


def _session_key(sid: str) -> str:
    return f"session:{sid}"


async def store_session(sid: str, user_id: str, ttl_seconds: int) -> None:
    """Вызывается при выдаче refresh-токена (регистрация/вход). Недоступный
    Redis тут не критичен -- сессия просто не будет отзываемой до логина,
    session_is_active в этом случае доверяет подписи JWT."""
    with contextlib.suppress(RedisError):
        await _client.set(_session_key(sid), user_id, ex=ttl_seconds)


async def session_is_active(sid: str, user_id: str) -> bool:
    """True -- можно выдавать новый access-токен по этому refresh-токену.
    Redis недоступен -- доверяем одной лишь подписи и сроку JWT (fail open,
    см. докстринг модуля); Redis доступен, но записи нет -- сессию явно
    отозвали (logout) или она никогда не создавалась с этим sid, в обоих
    случаях отказываем."""
    try:
        stored = await _client.get(_session_key(sid))
    except RedisError as e:
        logger.warning("Redis недоступен, проверка сессии пропущена (%s): %s", sid, e)
        return True
    return stored == user_id


async def revoke_session(sid: str) -> None:
    """logout: убрать сессию немедленно, не дожидаясь истечения
    REFRESH_TOKEN_TTL_SECONDS."""
    with contextlib.suppress(RedisError):
        await _client.delete(_session_key(sid))
