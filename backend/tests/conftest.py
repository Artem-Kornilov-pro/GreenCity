"""
Общие фикстуры для юнит-тестов backend/. Полностью офлайн:

* MongoDB подменяется mongomock_motor.AsyncMongoMockClient -- тот же
  асинхронный интерфейс (AsyncMongoClient из pymongo), что и db.py, но
  in-memory, без поднятого mongod;
* Redis подменяется fakeredis.aioredis.FakeRedis -- тот же интерфейс, что и
  redis.asyncio.Redis в cache.py;
* LLM НЕ вызывается ни в одном тесте -- реальные вызовы API стоят
  пользователю денег (см. test_llm_editor_request.py, где сетевой вызов
  замещается фальшивым клиентом).

db.py/projects.py импортируют коллекции по конкретным именам при своём
собственном импорте (`from db import users as users_col` в projects.py) --
поэтому подменять нужно оба места: db.users/db.projects (их видит
db.ensure_indexes(), вызываемый из lifespan main.py) И
projects.users_col/projects.projects_col (их видит вся остальная бизнес-логика
модуля projects.py). Один только db.py, без projects.py, для сквозных тестов
через projects_service ничего бы не изменил -- projects.py уже захватил свои
собственные ссылки на коллекции в момент импорта.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

sys.path[:0] = [str(ROOT / "parser"), str(ROOT / "backend")]

import cache  # noqa: E402
import db  # noqa: E402
import projects as projects_module  # noqa: E402
from fakeredis import aioredis as fakeredis_aioredis  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402
from parse_dxf import parse_dxf_file  # noqa: E402
from plant_catalog import load_catalog  # noqa: E402
from schemas import Scene  # noqa: E402


def _location_path(number: int) -> str:
    matches = glob.glob(str(ROOT / "locations" / f"{number:02d}_*" / "*.dxf"))
    if not matches:
        raise FileNotFoundError(f"нет фикстуры locations/{number:02d}_*/*.dxf")
    return matches[0]


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def location_scene_dict():
    """Сырые данные всех шести тестовых участков (locations/) -- парсятся
    один раз за весь прогон тестов (session-scope), т.к. ezdxf.readfile +
    геометрия -- не бесплатная операция, а участков много и они используются
    десятками тестов."""
    return {n: parse_dxf_file(_location_path(n)) for n in range(1, 7)}


@pytest.fixture(scope="session")
def location_scene(location_scene_dict):
    """То же самое, провалидированное в Scene (Pydantic) -- как сцена и живёт
    везде в приложении."""
    return {n: Scene.model_validate(d) for n, d in location_scene_dict.items()}


@pytest.fixture
def scene_01(location_scene):
    """Маленький, единственное здание -- дефолтная сцена для тестов, которым
    не важна сложная геометрия. Модели Pydantic неизменяемы по конвенции
    этого проекта (никто не мутирует чужую сцену на месте, кроме llm_editor,
    который явно создаёт новую) -- но копию всё равно возвращаем через
    model_copy(deep=True), чтобы тест, который может присвоить scene.objects
    (как это делает /api/generate-greenery), не испортил sessions-scope
    фикстуру для следующего теста."""
    return location_scene[1].model_copy(deep=True)


@pytest.fixture
def scene_02(location_scene):
    """Три здания, дворовое пространство -- сложнее геометрия, есть где
    развернуться courtyard_design/place_along и т.п."""
    return location_scene[2].model_copy(deep=True)


@pytest.fixture
def scene_06(location_scene):
    """Существующие дорожки/парк -- пригодится тестам, которым нужен
    непустой набор pedestrian_path/allowed зон одновременно."""
    return location_scene[6].model_copy(deep=True)


@pytest.fixture
async def fake_mongo(monkeypatch):
    """Подменяет и db.py, и уже захваченные projects.py ссылки на коллекции
    (см. докстринг модуля) -- отдельная in-memory база на каждый тест, без
    протечки данных между тестами.

    Сразу же создаёт индексы (db.ensure_indexes()) -- в реальном приложении
    это всегда происходит один раз при старте (main.py: lifespan) ДО первого
    запроса на регистрацию, поэтому уникальность username в проде обеспечена
    ещё до того, как кто-либо успел зарегистрироваться. Тест, использующий
    fake_mongo, должен видеть то же самое поведение (например, реальный
    DuplicateKeyError на второй регистрации с тем же именем), а не "повезло,
    что индекс никто не создал"."""
    client = AsyncMongoMockClient()
    database = client["greencity_test"]
    monkeypatch.setattr(db, "_client", client)
    monkeypatch.setattr(db, "db", database)
    monkeypatch.setattr(db, "users", database["users"])
    monkeypatch.setattr(db, "projects", database["projects"])
    monkeypatch.setattr(projects_module, "users_col", database["users"])
    monkeypatch.setattr(projects_module, "projects_col", database["projects"])
    await db.ensure_indexes()
    return database


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache, "_client", fake)
    return fake
