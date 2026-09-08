#!/usr/bin/env python3
"""
FastAPI-бэкенд для веб-редактора озеленения. Эндпоинты:
    POST /api/parse             -- DXF -> JSON-сцена (через parser/parse_dxf.py)
    POST /api/generate-greenery -- JSON-сцена -> JSON-сцена с добавленным
                                    озеленением (пока заглушка, см. докстринг)

Запуск (из папки backend/, в venv с requirements.txt из корня проекта):
    uvicorn main:app --reload --port 8000
"""

import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from schemas import Scene

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))
from parse_dxf import parse_dxf_file  # noqa: E402

app = FastAPI(title="GreenCity API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # прототип: фронтенд может стучаться с любого dev-порта
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/parse")
async def parse_dxf_endpoint(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "Ожидается файл .dxf")

    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            scene = parse_dxf_file(tmp.name)
        except Exception as e:
            raise HTTPException(400, f"Не удалось разобрать DXF: {e}")

    return scene


@app.post("/api/generate-greenery", response_model=Optional[Scene])
async def generate_greenery(scene: Scene):
    """
    Автоматически расставить озеленение (деревья/кустарники) на сцене.

    Вызывается с фронтенда по кнопке "Сгенерировать растительность
    автоматически" (frontend/src/App.tsx) -- туда уходит текущая сцена
    (та же структура, что отдаёт /api/parse, плюс любые правки, которые
    пользователь уже внёс вручную: подвинутые/удалённые/добавленные объекты),
    а фронтенд ожидает в ответ сцену той же формы обратно и полностью ею
    заменяет текущее состояние.

    ЗАГЛУШКА -- алгоритм должен реализовать другой разработчик. Ожидаемое
    поведение:

    1. `scene.boundary` -- граница участка, доступная для озеленения.
    2. Вычесть зоны из `scene.restrictions` с severity == "forbidden"
       (здания, коммуникации, дороги и т.п.). Отступ для каждой зоны
       зависит от вида посадки (дерево/кустарник) по нормам
       СНиП 2.07.01-89*/СП 42.13330.2016 -- см. таблицу в
       frontend/src/setbackNorms.ts (её стоит перенести сюда как единый
       источник правды вместо дублирования чисел на фронте и бэке).
    3. В оставшейся допустимой площади сгенерировать позиции посадок --
       новые SceneObject с type="tree"/"bush" (та же типизация, что и в
       parser/parse_dxf.py POINT_LAYER_RULES), с разумной плотностью и
       расстоянием друг от друга.
    4. Не трогать уже существующие объекты пользователя (здания, фонари,
       лавки, вручную расставленные деревья/кусты) -- только добавлять
       новые в `scene.objects`.
    5. Вернуть Scene с тем же boundary/restrictions/windows/canopies/meta
       и дополненным objects.

    Пока не реализовано -- возвращает None (фронтенд трактует это как
    "функция ещё не готова" и не трогает текущую сцену).
    """
    pass
