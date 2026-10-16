"""storage/db.py -- ensure_indexes() должен создавать индексы на реальном (пусть и
поддельном) клиенте и не падать, если Mongo недоступна вовсе."""

from pymongo.errors import PyMongoError

from storage import db


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
    assert await db.ensure_indexes() is False  # не должно поднять исключение -- см. докстринг db.py


async def test_indexes_are_created_once_mongo_comes_up_later(monkeypatch):
    # В docker compose бэкенд не ждёт MongoDB: база может подняться позже, и
    # уникальный индекс имён всё равно должен появиться.
    calls = []

    class _LateCollection:
        async def create_index(self, *args, **kwargs):
            calls.append(args)
            if len(calls) <= 2:
                raise PyMongoError("ещё не поднялась")

    monkeypatch.setattr(db, "users", _LateCollection())
    monkeypatch.setattr(db, "projects", _LateCollection())
    monkeypatch.setattr(db, "INDEX_RETRY_INTERVAL_S", 0)
    await db.ensure_indexes_when_available()
    assert calls == [("username",), ("username",), ("username",), ("owner_id",)]


def test_mongo_uri_is_a_valid_connection_string():
    # MONGO_URI читается один раз при импорте модуля из окружения -- здесь
    # просто проверяем форму значения, с которым модуль реально живёт в этом
    # тестовом прогоне (локально или в docker-compose, оба варианта -- валидная
    # mongodb:// строка, см. .env.example/docker-compose.yml).
    assert db.MONGO_URI.startswith("mongodb://")
