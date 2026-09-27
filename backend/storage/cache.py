"""
Redis -- для того, что можно потерять без вреда: ограничение частоты входа
и регистрации, кэш ответа /api/catalog и реестр активных сессий по
refresh-токенам (logout -- настоящий отзыв).

Всё это fail open: если Redis недоступен, лимиты не действуют, каталог
собирается напрямую, а refresh-токен проверяется только по подписи.
Недоступный вспомогательный сервис не роняет бэкенд.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.paths import ENV_FILE

# REDIS_URL можно задать в .env в корне репозитория (см. .env.example).
load_dotenv(ENV_FILE)

logger = logging.getLogger("greencity.cache")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Строки вместо bytes; короткий таймаут подключения, чтобы недоступный Redis
# не подвешивал запрос на системный таймаут TCP.
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
