"""Pydantic-модели для аутентификации и хранения проектов (db.py, projects.py,
auth.py). Отдельно от schemas.py -- тот описывает форму сцены (DXF -> JSON),
а это -- форму аккаунтов и проектов, разные по смыслу и жизненному циклу."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from schemas import Scene


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=200)  # нижняя граница -- в auth.validate_password, тут только защита от пустой строки/DoS


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    """Регистрация/вход выдают оба токена сразу: access_token -- для
    заголовка каждого запроса (короткоживущий, backend/auth.py), refresh_token
    -- чтобы получить новый access_token позже (/api/auth/refresh) без
    повторного ввода пароля."""

    access_token: str
    refresh_token: str
    username: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    username: str


class MeResponse(BaseModel):
    username: str


class ProjectSummary(BaseModel):
    """Для списка проектов -- без сцены: она может быть тяжёлой (район --
    тысячи объектов), а список нужен только чтобы выбрать, какой открыть."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scene: Scene


class ProjectUpdate(BaseModel):
    """Оба поля необязательны: можно переименовать проект, не трогая сцену,
    или сохранить сцену, не трогая имя."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    scene: Optional[Scene] = None


class Project(BaseModel):
    id: str
    name: str
    scene: Scene
    created_at: datetime
    updated_at: datetime
