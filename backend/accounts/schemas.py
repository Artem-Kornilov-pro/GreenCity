"""Pydantic-модели для аутентификации и хранения проектов (db.py, projects.py,
auth.py). Отдельно от schemas.py -- тот описывает форму сцены (DXF -> JSON),
а это -- форму аккаунтов и проектов, разные по смыслу и жизненному циклу."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from core.schemas import Scene


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=200)  # нижняя граница -- в auth.validate_password, тут только защита от пустой строки/DoS


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    """Регистрация/вход выдают оба токена сразу: access_token -- для
    заголовка каждого запроса (короткоживущий, backend/accounts/auth.py), refresh_token
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


class ProjectFile(BaseModel):
    """Файл проекта: документы и картинки по результату GreenPlan. Содержимое
    -- на диске (accounts/project_files.py), здесь -- описание."""

    id: str
    name: str = Field(description="Имя для сохранения: «Пояснительная записка — Двор.docx».")
    kind: str = Field(description="docx, explanations_json, explanations_csv, explanations_xlsx, dxf, export_check, visualization")
    media_type: str
    size: int = Field(description="Байт.")
    created_at: datetime
    auto: bool = Field(default=True, description="Подготовлен сам после GreenPlan: следующий запуск заменяет весь такой комплект.")


class ProjectSummary(BaseModel):
    """Для списка проектов -- без сцены: она может быть тяжёлой (район --
    тысячи объектов), а список нужен только чтобы выбрать, какой открыть."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    files_count: int = 0
    shared: bool = Field(default=False, description="Открыт доступ по ссылке.")


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
    files: list[ProjectFile] = []
    share_token: Optional[str] = Field(default=None, description="Ключ ссылки /share/<ключ>; None -- доступ по ссылке выключен.")


class SharedProject(BaseModel):
    """Проект по ссылке -- только просмотр: без владельца и ключа."""

    name: str
    scene: Scene
    updated_at: datetime
    files: list[ProjectFile] = []


class ShareResponse(BaseModel):
    share_token: Optional[str]
