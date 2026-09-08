#!/usr/bin/env python3
"""
FastAPI-бэкенд для веб-редактора озеленения. Пока один эндпоинт: приём DXF,
разбор через parser/parse_dxf.py, отдача сцены (boundary/restrictions/objects)
фронтенду в виде одного JSON.

Запуск (из папки backend/, в venv с requirements.txt из корня проекта):
    uvicorn main:app --reload --port 8000
"""

import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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
