"""
Нагрузочный прогон GreenCity (Locust): что выдерживает сервер и сколько
запросов в секунду.

Три типа пользователей, как на настоящем стенде:

* Visitor -- открывает сайт: главная страница и её статика (JS, CSS).
* ApiReader -- лёгкие запросы API: health, каталог растений, типы слоёв.
* Engineer -- рабочий цикл: загрузка DXF -> GreenPlan -> объяснения (Excel)
  -> выгрузка DXF. Платные запросы (текст-обоснование, визуализации) и
  регистрация не делаются: это деньги и мусор в базе.

Тяжёлые эндпоинты ограничены по IP (backend/api/protection.py: 30 разборов,
60 GreenPlan за 10 минут), а прогон идёт с одного адреса -- ответы 429
считаются отдельно (имя запроса с пометкой «[429 лимит]»), а не ошибкой
сервера. 503 -- очередь тяжёлых расчётов полна (core/concurrency.py), это
и есть предел сервера. Чтобы мерить тяжёлую часть без лимита, на сервере
задают RATE_LIMIT_MULTIPLIER=0.

    make loadtest HOST=https://baobab24.ru                 # веб-интерфейс http://localhost:8089
    make loadtest-headless HOST=https://baobab24.ru        # ступенчатый прогон, отчёт в loadtest-report/

Профиль ступеней -- StepShape (переменные LOAD_STEP_USERS, LOAD_STEP_SECONDS,
LOAD_MAX_USERS); состав пользователей -- веса классов ниже или
--class-picker в веб-интерфейсе.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from locust import HttpUser, LoadTestShape, between, task

ROOT = Path(__file__).resolve().parents[2]
DXF_PATH = Path(os.environ.get("LOAD_DXF") or ROOT / "locations" / "location_test" / "01_school" / "01_school.dxf")
DXF_BYTES = DXF_PATH.read_bytes()
ASSET_RE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')
# Множитель пауз «на подумать» между запросами: 1 -- как у человека, 0 --
# без пауз (предельный RPS).
THINK = float(os.environ.get("LOAD_THINK") or 1)


def think(low: float, high: float):
    return between(low * THINK, high * THINK)


def _check(response, name: str) -> None:
    """429 (лимит по IP) и 503 (очередь расчётов полна) -- отдельными строками
    отчёта: первое -- защита сервера от одного адреса, второе -- его предел."""
    if response.status_code == 429:
        response.request_meta["name"] = f"{name} [429 лимит]"
        response.success()
    elif response.status_code == 503:
        response.request_meta["name"] = f"{name} [503 очередь полна]"
        response.failure("503: очередь тяжёлых расчётов полна")
    elif response.status_code >= 400:
        response.failure(f"{response.status_code}: {response.text[:120]}")


class Visitor(HttpUser):
    """Открывает сайт: страница и её статика."""

    weight = 3
    wait_time = think(1, 4)

    def on_start(self) -> None:
        page = self.client.get("/", name="GET / (страница)")
        self.assets = ASSET_RE.findall(page.text) if page.ok else []

    @task
    def open_site(self) -> None:
        self.client.get("/", name="GET / (страница)")
        for asset in self.assets:
            kind = "js" if asset.endswith(".js") else "css" if asset.endswith(".css") else "другое"
            self.client.get(asset, name=f"GET /assets/*.{kind}")


class ApiReader(HttpUser):
    """Лёгкие запросы API без лимитов."""

    weight = 3
    wait_time = think(0.5, 2)

    @task(3)
    def health(self) -> None:
        self.client.get("/api/health", name="GET /api/health")

    @task(2)
    def catalog(self) -> None:
        self.client.get("/api/catalog", name="GET /api/catalog")

    @task(1)
    def layer_kinds(self) -> None:
        self.client.get("/api/layer-kinds", name="GET /api/layer-kinds")


class Engineer(HttpUser):
    """Рабочий цикл над небольшим участком: разбор, GreenPlan, Excel, DXF."""

    weight = 1
    wait_time = think(5, 15)

    @task
    def full_cycle(self) -> None:
        with self.client.post(
            "/api/parse", files={"file": (DXF_PATH.name, DXF_BYTES, "application/dxf")}, name="POST /api/parse", catch_response=True
        ) as r:
            _check(r, "POST /api/parse")
            if r.status_code != 200:
                return
            scene = r.json()
        with self.client.post("/api/greenplan/generate", json={"scene": scene}, name="POST /api/greenplan/generate", catch_response=True) as r:
            _check(r, "POST /api/greenplan/generate")
            if r.status_code != 200:
                return
            plan = r.json()
        body = {"scene": plan["scene"], "assignments": plan["assignments"], "rejections": plan.get("rejections") or {}}
        with self.client.post(
            "/api/greenplan/explanations?format=xlsx", json=body, name="POST /api/greenplan/explanations (xlsx)", catch_response=True
        ) as r:
            _check(r, "POST /api/greenplan/explanations (xlsx)")
        with self.client.post("/api/export-dxf", json=plan["scene"], name="POST /api/export-dxf", catch_response=True) as r:
            _check(r, "POST /api/export-dxf")


if os.environ.get("LOAD_SHAPE") == "step":

    class StepShape(LoadTestShape):
        """Ступени: каждые LOAD_STEP_SECONDS пользователей больше на
        LOAD_STEP_USERS, до LOAD_MAX_USERS; затем прогон заканчивается.
        Только при LOAD_SHAPE=step: Locust берёт форму нагрузки, если класс
        есть в файле, а в веб-интерфейсе число пользователей задаётся руками."""

        step_users = int(os.environ.get("LOAD_STEP_USERS") or 20)
        step_seconds = int(os.environ.get("LOAD_STEP_SECONDS") or 60)
        max_users = int(os.environ.get("LOAD_MAX_USERS") or 200)

        def tick(self):
            step = int(self.get_run_time() // self.step_seconds) + 1
            users = step * self.step_users
            if users > self.max_users:
                return None
            return users, self.step_users
