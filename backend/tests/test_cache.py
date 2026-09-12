"""cache.py -- всё "fail open": недоступный Redis не должен блокировать
вход/регистрацию/каталог, см. докстринг модуля. fake_redis (conftest.py)
подменяет реальный клиент рабочей fakeredis-реализацией, поэтому happy-path
тестируется по-настоящему (INCR/EXPIRE/GET/SET), а поведение "Redis лежит"
проверяется отдельно, подсовывая клиент, который на любой вызов бросает
RedisError."""

import cache
import pytest
from redis.exceptions import RedisError


class _BrokenRedis:
    """Имитирует полностью недоступный Redis -- любой метод бросает
    RedisError, как и настоящий клиент при обрыве соединения."""

    async def _fail(self, *args, **kwargs):
        raise RedisError("подключение недоступно")

    def __getattr__(self, name):
        return self._fail


@pytest.fixture
def broken_redis(monkeypatch):
    monkeypatch.setattr(cache, "_client", _BrokenRedis())


# --- Ограничение частоты -----------------------------------------------------


async def test_rate_limit_allows_up_to_max_attempts(fake_redis):
    key = "rl:test:1.2.3.4"
    for _ in range(5):
        assert await cache.check_rate_limit(key, max_attempts=5, window_seconds=60) is True
    assert await cache.check_rate_limit(key, max_attempts=5, window_seconds=60) is False


async def test_rate_limit_is_per_key(fake_redis):
    assert await cache.check_rate_limit("rl:a", max_attempts=1, window_seconds=60) is True
    assert await cache.check_rate_limit("rl:a", max_attempts=1, window_seconds=60) is False
    # Другой ключ -- независимый счётчик, не должен быть затронут ключом "a"
    assert await cache.check_rate_limit("rl:b", max_attempts=1, window_seconds=60) is True


async def test_reset_rate_limit_clears_the_counter(fake_redis):
    key = "rl:test:reset"
    assert await cache.check_rate_limit(key, max_attempts=1, window_seconds=60) is True
    assert await cache.check_rate_limit(key, max_attempts=1, window_seconds=60) is False
    await cache.reset_rate_limit(key)
    assert await cache.check_rate_limit(key, max_attempts=1, window_seconds=60) is True


async def test_rate_limit_fails_open_when_redis_unavailable(broken_redis):
    # Недоступный Redis -- лимит считается НЕ превышенным (иначе весь вход
    # заблокировался бы у всех пользователей разом из-за инфраструктурного сбоя).
    assert await cache.check_rate_limit("rl:whatever", max_attempts=1, window_seconds=60) is True


async def test_reset_rate_limit_is_silent_when_redis_unavailable(broken_redis):
    await cache.reset_rate_limit("rl:whatever")  # не должно поднять исключение


# --- Кэш каталога -------------------------------------------------------------


async def test_get_cached_returns_none_when_key_missing(fake_redis):
    assert await cache.get_cached("no-such-key") is None


async def test_set_then_get_cached_round_trips(fake_redis):
    await cache.set_cached("catalog:v1", '{"a": 1}', ttl_seconds=30)
    assert await cache.get_cached("catalog:v1") == '{"a": 1}'


async def test_get_cached_fails_open_to_none_when_redis_unavailable(broken_redis):
    assert await cache.get_cached("catalog:v1") is None


async def test_set_cached_is_silent_when_redis_unavailable(broken_redis):
    await cache.set_cached("catalog:v1", "value", ttl_seconds=30)  # не должно поднять исключение


# --- Сессии по refresh-токенам ------------------------------------------------


async def test_session_is_active_true_right_after_store(fake_redis):
    await cache.store_session("sid-1", "user-1", ttl_seconds=3600)
    assert await cache.session_is_active("sid-1", "user-1") is True


async def test_session_is_active_false_for_wrong_user(fake_redis):
    await cache.store_session("sid-1", "user-1", ttl_seconds=3600)
    # Тот же sid, но не тот пользователь -- не должно засчитаться активной
    assert await cache.session_is_active("sid-1", "user-2") is False


async def test_session_is_active_false_when_never_stored(fake_redis):
    assert await cache.session_is_active("unknown-sid", "user-1") is False


async def test_revoke_session_makes_it_inactive_immediately(fake_redis):
    await cache.store_session("sid-1", "user-1", ttl_seconds=3600)
    await cache.revoke_session("sid-1")
    assert await cache.session_is_active("sid-1", "user-1") is False


async def test_session_is_active_fails_open_when_redis_unavailable(broken_redis):
    # Redis недоступен -- сессия считается активной (доверяем подписи JWT,
    # см. докстринг cache.py и auth.py) -- иначе logout/недоступность Redis
    # разлогинил бы всех пользователей разом.
    assert await cache.session_is_active("sid-1", "user-1") is True


async def test_store_and_revoke_session_are_silent_when_redis_unavailable(broken_redis):
    await cache.store_session("sid-1", "user-1", ttl_seconds=3600)
    await cache.revoke_session("sid-1")
