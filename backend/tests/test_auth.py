"""auth.py -- хэширование паролей, access/refresh JWT, require_user-зависимость.
session_is_active (Redis) не мокается здесь напрямую -- используется fake_redis
из conftest.py, чтобы проверить настоящую логику отзыва, а не только заглушку."""

import time

import auth
import jwt
import pytest
from fastapi import HTTPException

# --- Валидация -----------------------------------------------------------


def test_validate_username_accepts_allowed_charset():
    assert auth.validate_username("user_name.1") == "user_name.1"


def test_validate_username_strips_surrounding_whitespace():
    assert auth.validate_username("  bob  ") == "bob"


@pytest.mark.parametrize("bad", ["ab", "a" * 33, "имя-кириллица", "bad name", "bad!name"])
def test_validate_username_rejects_invalid(bad):
    with pytest.raises(auth.AuthError):
        auth.validate_username(bad)


def test_validate_password_rejects_too_short():
    with pytest.raises(auth.AuthError):
        auth.validate_password("12345")


def test_validate_password_accepts_min_length():
    assert auth.validate_password("123456") == "123456"


# --- Пароли ----------------------------------------------------------------


def test_hash_and_verify_password_round_trip():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True
    assert auth.verify_password("wrong password", hashed) is False


def test_verify_password_with_malformed_hash_returns_false_not_raises():
    # Битый/чужого формата хэш в базе -- не должен ронять запрос 500-й.
    assert auth.verify_password("anything", "not-a-real-bcrypt-hash") is False


# --- JWT: access/refresh -----------------------------------------------------


def test_access_token_round_trips_and_has_correct_type():
    token = auth.create_access_token("user-1", "alice")
    payload = auth.decode_token(token)
    assert payload["sub"] == "user-1"
    assert payload["username"] == "alice"
    assert payload["type"] == "access"


def test_refresh_token_has_a_unique_sid_each_time():
    token_a, sid_a = auth.create_refresh_token("user-1", "alice")
    token_b, sid_b = auth.create_refresh_token("user-1", "alice")
    assert sid_a != sid_b
    assert token_a != token_b
    payload = auth.decode_token(token_a)
    assert payload["sid"] == sid_a
    assert payload["type"] == "refresh"


def test_decode_token_returns_none_for_garbage():
    assert auth.decode_token("not-a-jwt-at-all") is None


def test_decode_token_returns_none_for_expired_token():
    expired_payload = {"sub": "user-1", "username": "alice", "type": "access", "exp": int(time.time()) - 10}
    expired_token = jwt.encode(expired_payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    assert auth.decode_token(expired_token) is None


def test_decode_token_returns_none_for_wrong_signature():
    token = jwt.encode(
        {"sub": "x", "type": "access", "exp": int(time.time()) + 60}, "a-different-secret-key-of-proper-length", algorithm=auth.JWT_ALGORITHM
    )
    assert auth.decode_token(token) is None


# --- refresh_access_token (использует Redis через cache.py) ------------------


async def test_refresh_access_token_succeeds_with_active_session(fake_redis):
    refresh_token, sid = auth.create_refresh_token("user-1", "alice")
    import cache

    await cache.store_session(sid, "user-1", ttl_seconds=3600)
    access_token, username = await auth.refresh_access_token(refresh_token)
    assert username == "alice"
    assert auth.decode_token(access_token)["type"] == "access"


async def test_refresh_access_token_rejects_revoked_session(fake_redis):
    refresh_token, sid = auth.create_refresh_token("user-1", "alice")
    import cache

    await cache.store_session(sid, "user-1", ttl_seconds=3600)
    await cache.revoke_session(sid)
    with pytest.raises(auth.AuthError):
        await auth.refresh_access_token(refresh_token)


async def test_refresh_access_token_rejects_access_token_used_as_refresh(fake_redis):
    access_token = auth.create_access_token("user-1", "alice")
    with pytest.raises(auth.AuthError):
        await auth.refresh_access_token(access_token)


async def test_refresh_access_token_rejects_garbage_token(fake_redis):
    with pytest.raises(auth.AuthError):
        await auth.refresh_access_token("not-a-jwt")


async def test_refresh_access_token_fails_open_when_redis_unavailable(monkeypatch):
    """Сессия без sid в Redis, но Redis вообще недоступен -- session_is_active
    fail-open'ит в True (см. cache.py), поэтому refresh должен пройти."""
    import cache
    from redis.exceptions import RedisError

    class _BrokenRedis:
        async def _fail(self, *a, **kw):
            raise RedisError("недоступен")

        def __getattr__(self, name):
            return self._fail

    monkeypatch.setattr(cache, "_client", _BrokenRedis())
    refresh_token, _sid = auth.create_refresh_token("user-1", "alice")
    access_token, username = await auth.refresh_access_token(refresh_token)
    assert username == "alice"


# --- require_user (FastAPI-зависимость) --------------------------------------


def test_require_user_accepts_valid_bearer_access_token():
    token = auth.create_access_token("user-1", "alice")
    user = auth.require_user(authorization=f"Bearer {token}")
    assert user.id == "user-1"
    assert user.username == "alice"


def test_require_user_rejects_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        auth.require_user(authorization=None)
    assert exc_info.value.status_code == 401


def test_require_user_rejects_malformed_header():
    with pytest.raises(HTTPException):
        auth.require_user(authorization="NotBearer abc")


def test_require_user_rejects_refresh_token_used_as_access():
    refresh_token, _sid = auth.create_refresh_token("user-1", "alice")
    with pytest.raises(HTTPException) as exc_info:
        auth.require_user(authorization=f"Bearer {refresh_token}")
    assert exc_info.value.status_code == 401
