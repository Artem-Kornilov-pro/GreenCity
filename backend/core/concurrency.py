"""
Тяжёлая синхронная работа (геометрия shapely, ezdxf, сборка документов) из
асинхронных обработчиков: выполняется в пуле потоков, чтобы не блокировать
event loop, и не больше HEAVY_CONCURRENCY задач одновременно на процесс.

Потоки одного процесса делят GIL, поэтому две тяжёлые задачи параллельно
считаются не быстрее, а обе вдвое медленнее. Лишние запросы ждут слота в
очереди. Очередь ограничена: при HEAVY_QUEUE_MAX ждущих или ожидании дольше
HEAVY_WAIT_S запрос получает ServerBusy (ответ 503), а не висит минутами.
Лёгкие запросы (каталог, аккаунты, health) слотов не ждут.
"""

from __future__ import annotations

import asyncio
import functools
import os
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import anyio.to_thread
from dotenv import load_dotenv

from core.paths import ENV_FILE

# Настройки ниже можно задать в .env в корне репозитория (см. .env.example).
load_dotenv(ENV_FILE)

HEAVY_CONCURRENCY = max(1, int(os.environ.get("HEAVY_CONCURRENCY", "1")))
HEAVY_QUEUE_MAX = max(0, int(os.environ.get("HEAVY_QUEUE_MAX", "8")))
HEAVY_WAIT_S = float(os.environ.get("HEAVY_WAIT_S", "120"))


class ServerBusy(Exception):
    """Очередь тяжёлых задач переполнена или ожидание слота истекло."""

    def __init__(self, detail: str, retry_after_s: int = 30):
        super().__init__(detail)
        self.detail = detail
        self.retry_after_s = retry_after_s


class _Gate:
    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(HEAVY_CONCURRENCY)
        self.waiting = 0


# Свой затвор на каждый event loop: примитивы asyncio привязаны к циклу, а
# TestClient в тестах поднимает новый цикл на каждого клиента.
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate] = weakref.WeakKeyDictionary()


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = _Gate()
    return gate


@asynccontextmanager
async def heavy_slot() -> AsyncIterator[None]:
    gate = _gate()
    if gate.semaphore.locked() and gate.waiting >= HEAVY_QUEUE_MAX:
        raise ServerBusy("Сервер занят расчётами других пользователей. Повторите через минуту.")
    gate.waiting += 1
    try:
        await asyncio.wait_for(gate.semaphore.acquire(), timeout=HEAVY_WAIT_S)
    except TimeoutError as e:
        raise ServerBusy("Сервер занят: запрос не дождался очереди. Повторите через пару минут.", 120) from e
    finally:
        gate.waiting -= 1
    try:
        yield
    finally:
        gate.semaphore.release()


async def run_heavy[T](func: Callable[..., T], *args, **kwargs) -> T:
    """func(*args, **kwargs) в пуле потоков, заняв тяжёлый слот. Отменить уже
    начатый расчёт нельзя: слот освобождается, когда поток закончит."""
    async with heavy_slot():
        return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))


async def run_light[T](func: Callable[..., T], *args, **kwargs) -> T:
    """Короткая блокирующая операция (чтение файла, запись на диск) в пуле
    потоков без тяжёлого слота."""
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))
