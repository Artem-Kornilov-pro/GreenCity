"""core/concurrency.py -- тяжёлая работа в пуле потоков, слот на процесс и
ограниченная очередь."""

import asyncio
import threading

import pytest

from core import concurrency
from core.concurrency import ServerBusy, heavy_slot, run_heavy


async def test_run_heavy_runs_in_a_worker_thread():
    assert await run_heavy(threading.get_ident) != threading.get_ident()


async def test_run_heavy_passes_arguments_and_returns_result():
    assert await run_heavy(lambda a, b=0: a + b, 2, b=3) == 5


async def _hold_slot(release: asyncio.Event) -> None:
    async with heavy_slot():
        await release.wait()


async def test_waiting_requests_run_one_after_another(monkeypatch):
    monkeypatch.setattr(concurrency, "HEAVY_QUEUE_MAX", 4)
    release = asyncio.Event()
    holder = asyncio.create_task(_hold_slot(release))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(run_heavy(lambda: "готово"))
    await asyncio.sleep(0.05)
    assert not waiter.done()  # ждёт, пока слот занят
    release.set()
    assert await waiter == "готово"
    await holder


async def test_full_queue_is_rejected_at_once(monkeypatch):
    monkeypatch.setattr(concurrency, "HEAVY_QUEUE_MAX", 1)
    release = asyncio.Event()
    holder = asyncio.create_task(_hold_slot(release))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(run_heavy(lambda: 1))
    await asyncio.sleep(0)
    with pytest.raises(ServerBusy):
        async with heavy_slot():
            pass
    release.set()
    assert await waiter == 1
    await holder


async def test_waiting_too_long_is_rejected(monkeypatch):
    monkeypatch.setattr(concurrency, "HEAVY_WAIT_S", 0.05)
    release = asyncio.Event()
    holder = asyncio.create_task(_hold_slot(release))
    await asyncio.sleep(0)
    with pytest.raises(ServerBusy) as err:
        async with heavy_slot():
            pass
    assert err.value.retry_after_s > 0
    release.set()
    await holder


async def test_slot_is_released_after_an_error():
    with pytest.raises(ValueError):
        await run_heavy(lambda: (_ for _ in ()).throw(ValueError("сбой")))
    assert await run_heavy(lambda: "снова свободен") == "снова свободен"
