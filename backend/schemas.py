"""
Pydantic-модели сцены -- ровно та же структура, что parser/parse_dxf.py кладёт
в JSON и frontend/src/types.ts описывает на стороне фронтенда. Держать все три
описания синхронными приходится вручную (нет общей кодогенерации схемы) --
при изменении формы сцены правь все три места.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class Point2(BaseModel):
    x: float
    z: float


class Point3(BaseModel):
    x: float
    y: float
    z: float


class Boundary(BaseModel):
    polygon: list[Point2]
    sourceLayer: str


class RestrictionZone(BaseModel):
    id: str
    type: str
    name: str
    polygon: list[Point2]
    severity: Literal["forbidden", "warning", "allowed"]
    minDistance: float
    message: str
    maxHeight: Optional[float] = None


class SceneObject(BaseModel):
    id: str
    type: str
    model: str
    position: Point3
    rotation: float
    scale: float
    metadata: dict


class SceneMeta(BaseModel):
    scale: float
    insunits: int
    origin: dict
    buildingCount: int
    pointObjectCount: int


class Scene(BaseModel):
    boundary: Optional[Boundary]
    restrictions: list[RestrictionZone]
    objects: list[SceneObject]
    windows: list[list[Point3]] = []
    canopies: list[list[Point3]] = []
    meta: SceneMeta
