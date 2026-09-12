"""
Подключение к MongoDB -- хранилище пользователей и их проектов (по требованию
задачи: "их хранение логично реализовать через mongoDB"). Ничего из основной
логики парсинга/генерации/правки текстом Mongo не касается -- та сцена,
что человек сейчас редактирует, как жила в состоянии фронтенда, так и живёт;
в базу попадает только то, что явно сохранили как проект (projects.py).

Клиент асинхронный (pymongo.AsyncMongoClient -- нативная поддержка асинхронной
работы появилась в самом pymongo, отдельный пакет motor для этого не нужен):
сервис рассчитан на много одновременных пользователей, а блокирующий клиент
на каждый запрос к базе занимал бы поток из ограниченного пула FastAPI, а не
только время самого запроса к Mongo.

MONGO_URI берётся из окружения: локально -- из .env, в Docker -- уже из
env_file/docker-compose (сервис mongo, см. docker-compose.yml). Без
переменной подключение идёт на localhost -- это позволяет поднять локальный
mongod для разработки без .env вовсе.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

# Локально .env лежит в корне репозитория -- MONGO_URI при желании задаётся
# там же (см. .env.example). load_dotenv без override уже выставленные
# переменные не перетирает, поэтому повторный вызов из других модулей
# (auth.py, llm_editor.py) безвреден.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("greencity.db")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "greencity")

# serverSelectionTimeoutMS короткий и явный: конструктор клиента сам по себе
# не блокирует и не поднимает исключение (ленивое подключение), но операции
# ниже -- реальный поход на сервер. Без таймаута недоступный Mongo держал бы
# запрос 30 секунд (значение по умолчанию) вместо быстрой деградации до
# "проекты недоступны, остальное работает".
_client = AsyncMongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = _client[MONGO_DB_NAME]

users = db["users"]
projects = db["projects"]


async def ensure_indexes() -> None:
    """Вызывается один раз при старте приложения (main.py: lifespan). У
    асинхронного клиента, в отличие от синхронного, нет операций до
    работающего event loop -- индексы нельзя создать прямо при импорте
    модуля, как раньше. create_index идемпотентен (повторный вызов при
    каждом рестарте -- не ошибка, Mongo просто убеждается, что индекс уже
    такой, какой нужен)."""
    try:
        await users.create_index("username", unique=True)
        await projects.create_index("owner_id")
    except PyMongoError as e:
        # MongoDB не настроена/недоступна -- деградирует только регистрация и
        # проекты (см. main.py: там же ловится PyMongoError на каждый запрос),
        # а не весь бэкенд: парсинг DXF, генерация и правка текстом с аккаунтами
        # не связаны и не должны падать из-за недоступной базы.
        logger.warning("MongoDB недоступна при старте (%s) -- регистрация и проекты не будут работать: %s", MONGO_URI, e)
