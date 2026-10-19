"""
Подключение к MongoDB -- пользователи и их проекты. Остальная логика базу не
использует: редактируемая сцена живёт во фронтенде, в базу попадает только
сохранённый проект.

Клиент асинхронный (pymongo.AsyncMongoClient). MONGO_URI -- из окружения,
по умолчанию localhost.
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from core.paths import ENV_FILE

# MONGO_URI можно задать в .env в корне репозитория (см. .env.example).
load_dotenv(ENV_FILE)

logger = logging.getLogger("greencity.db")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "greencity")

# Короткий таймаут выбора сервера: недоступная база должна быстро давать
# «проекты недоступны», а не держать запрос 30 секунд.
_client = AsyncMongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = _client[MONGO_DB_NAME]

users = db["users"]
projects = db["projects"]


async def _create_indexes() -> None:
    await users.create_index("username", unique=True)
    await projects.create_index("owner_id")
    # Ключ ссылки на проект (/share/<ключ>) -- уникален; у проектов без
    # ссылки его нет, поэтому индекс разреженный.
    await projects.create_index("share_token", unique=True, sparse=True)


async def ensure_indexes() -> bool:
    """Создаёт индексы; вызывается при старте приложения (lifespan).
    create_index идемпотентен. False -- база недоступна."""
    try:
        await _create_indexes()
    except PyMongoError as e:
        # База недоступна -- не работают только аккаунты и проекты, остальной
        # бэкенд с ними не связан.
        logger.warning("MongoDB недоступна при старте (%s) -- регистрация и проекты не будут работать: %s", MONGO_URI, e)
        return False
    return True


# Как часто и сколько раз повторять создание индексов, если база поднялась
# позже бэкенда: в docker compose бэкенд не ждёт MongoDB (на процессоре без
# AVX mongo:7 не стартует вовсе, а редактор работает и без неё).
INDEX_RETRY_INTERVAL_S = 5.0
INDEX_RETRY_ATTEMPTS = 120


async def ensure_indexes_when_available() -> None:
    """Повторяет ensure_indexes(), пока база не станет доступна: без индекса
    имя пользователя не было бы уникальным."""
    for _ in range(INDEX_RETRY_ATTEMPTS):
        await asyncio.sleep(INDEX_RETRY_INTERVAL_S)
        try:
            await _create_indexes()
        except PyMongoError:
            continue
        logger.info("MongoDB доступна, индексы созданы")
        return
