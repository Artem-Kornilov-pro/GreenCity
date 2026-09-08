#!/usr/bin/env python3
"""
FastAPI-бэкенд для веб-редактора озеленения. Эндпоинты:
    POST /api/parse             -- DXF -> JSON-сцена (через parser/parse_dxf.py)
    POST /api/generate-greenery -- JSON-сцена (+ опциональные query-параметры
                                    species/grid_spacing_m/min_tree_spacing_m)
                                    -> JSON-сцена с добавленными деревьями.
                                    Полное описание параметров и алгоритма --
                                    backend/README.md.

Запуск (из папки backend/, в venv с requirements.txt из корня проекта):
    uvicorn main:app --reload --port 8000
"""

import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from greenery_generator import (
    DEFAULT_GRID_SPACING_M,
    DEFAULT_MIN_TREE_SPACING_M,
    DEFAULT_TREE_SPECIES,
    MAX_ALLOWED_GRID_SPACING_M,
    MIN_ALLOWED_GRID_SPACING_M,
    generate_trees,
)
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
async def generate_greenery(
    scene: Scene,
    species: Optional[str] = Query(
        default=None,
        description=(
            "Вид дерева (например 'Липа мелколистная'). Влияет на отступы "
            "от ограничений, ЕСЛИ вид описан в "
            "setback_norms.SPECIES_SETBACK_OVERRIDES; для неизвестных видов "
            "используется общая норма для дерева. По умолчанию -- "
            f"{DEFAULT_TREE_SPECIES!r}."
        ),
    ),
    grid_spacing_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_GRID_SPACING_M,
        le=MAX_ALLOWED_GRID_SPACING_M,
        description=(
            "Шаг регулярной сетки кандидатных точек посадки, в метрах. "
            f"Меньше -- гуще посадка. По умолчанию {DEFAULT_GRID_SPACING_M} м. "
            f"Допустимый диапазон: {MIN_ALLOWED_GRID_SPACING_M}-{MAX_ALLOWED_GRID_SPACING_M} м."
        ),
    ),
    min_tree_spacing_m: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Минимальное расстояние между стволами уже отобранных "
            f"сгенерированных деревьев, в метрах. По умолчанию {DEFAULT_MIN_TREE_SPACING_M} м."
        ),
    ),
):
    """
    Автоматически расставить озеленение на сцене.

    Вызывается с фронтенда по кнопке "Сгенерировать растительность
    автоматически" (frontend/src/App.tsx) -- туда уходит текущая сцена
    (та же структура, что отдаёт /api/parse, плюс любые правки, которые
    пользователь уже внёс вручную: подвинутые/удалённые/добавленные объекты),
    а фронтенд ожидает в ответ сцену той же формы обратно и полностью ею
    заменяет текущее состояние.

    РЕАЛИЗОВАНО (greenery_generator.py): только деревья, детерминированным
    demo-генератором по сетке (ТЗ п.16-17) -- см. docstring generate_trees().
    Сажает только внутри явных зон озеленения (severity == "allowed",
    газон), избегая forbidden И warning зон (парковка/дорожки/сети) с
    отступом по виду посадки -- подробности и обоснование см. в
    greenery_generator.py::_keep_out_shapes. Кустарники/газон -- TODO, см.
    docstring greenery_generator.py.

    Существующие объекты пользователя (здания, фонари, лавки, вручную
    расставленные деревья/кусты) не трогаем -- только добавляем новые в
    scene.objects, остальные поля сцены (boundary/restrictions/windows/
    canopies/meta) возвращаем как есть.

    species/grid_spacing_m/min_tree_spacing_m -- параметры запроса (query
    string, НЕ часть тела Scene -- контракт тела менять не хотелось, раз он
    уже согласован с фронтендом). Не переданы -- берутся дефолты из
    greenery_generator.py. Полное описание см. в backend/README.md.

    ПРИМЕЧАНИЕ по контракту: раньше эндпоинт всегда возвращал None ("функция
    ещё не готова", см. api.ts/App.tsx на фронте). Теперь для деревьев
    алгоритм реализован, поэтому возвращаем реальную Scene всегда, даже если
    новых деревьев добавить некуда (пустая допустимая площадь) -- это
    легитимный результат работы, а не "не реализовано". None пока оставлен
    в response_model на случай будущих сценариев без места для растений,
    которые лучше явно отличать от "добавили 0 деревьев"; сейчас функция его
    не возвращает.
    """
    new_trees = generate_trees(
        scene,
        species=species,
        grid_spacing_m=grid_spacing_m,
        min_tree_spacing_m=min_tree_spacing_m,
    )
    scene.objects = [*scene.objects, *new_trees]
    return scene
