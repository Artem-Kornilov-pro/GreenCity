"""
Подключение к MongoDB -- пользователи и их проекты. Остальная логика базу не
использует: редактируемая сцена живёт во фронтенде, в базу попадает только
сохранённый проект.

Клиент асинхронный (pymongo.AsyncMongoClient). MONGO_URI -- из окружения,
по умолчанию localhost.
"""

from __future__ import annotations

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


async def ensure_indexes() -> None:
    """Создаёт индексы; вызывается один раз при старте приложения
    (lifespan). create_index идемпотентен."""
    try:
        await users.create_index("username", unique=True)
        await projects.create_index("owner_id")
    except PyMongoError as e:
        # База недоступна -- не работают только аккаунты и проекты, остальной
        # бэкенд с ними не связан.
        logger.warning("MongoDB недоступна при старте (%s) -- регистрация и проекты не будут работать: %s", MONGO_URI, e)
