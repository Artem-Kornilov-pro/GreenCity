"""db.py -- ensure_indexes() должен создавать индексы на реальном (пусть и
поддельном) клиенте и не падать, если Mongo недоступна вовсе."""

import db
from pymongo.errors import PyMongoError


async def test_ensure_indexes_creates_unique_username_index(fake_mongo):
    await db.ensure_indexes()
    indexes = await db.users.index_information()
    assert any(spec.get("unique") for name, spec in indexes.items() if name != "_id_")


async def test_ensure_indexes_creates_owner_id_index_on_projects(fake_mongo):
    await db.ensure_indexes()
    indexes = await db.projects.index_information()
    assert any("owner_id" in str(spec.get("key")) for name, spec in indexes.items() if name != "_id_")


async def test_ensure_indexes_does_not_raise_when_mongo_unavailable(monkeypatch):
    class _BrokenCollection:
        async def create_index(self, *args, **kwargs):
            raise PyMongoError("недоступна")

    monkeypatch.setattr(db, "users", _BrokenCollection())
    monkeypatch.setattr(db, "projects", _BrokenCollection())
    await db.ensure_indexes()  # не должно поднять исключение -- см. докстринг db.py


def test_mongo_uri_is_a_valid_connection_string():
    # MONGO_URI читается один раз при импорте модуля из окружения -- здесь
    # просто проверяем форму значения, с которым модуль реально живёт в этом
    # тестовом прогоне (локально или в docker-compose, оба варианта -- валидная
    # mongodb:// строка, см. .env.example/docker-compose.yml).
    assert db.MONGO_URI.startswith("mongodb://")
