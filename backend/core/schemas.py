"""
Модель сцены участка -- общий формат парсера (parser/parse_dxf.py), бэкенда и
фронтенда (frontend/src/types.ts). При изменении формы сцены правьте все три.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Point2(BaseModel):
    """Точка на плане, метры от центра участка: x -- на восток, z -- на север."""

    x: float
    z: float


class Point3(BaseModel):
    """Точка в пространстве: y -- высота, метры."""

    x: float
    y: float
    z: float


class Boundary(BaseModel):
    """Граница участка."""

    polygon: list[Point2]
    sourceLayer: str = Field(description="Слой исходного чертежа.")


class RestrictionZone(BaseModel):
    """Зона ограничения: здание, дорога, дорожка, подземная сеть с охранным
    коридором, площадка -- или разрешённая зона (газон)."""

    id: str
    type: str = Field(description="building, road, pedestrian_path, water_pipeline, heat_network, gas_pipeline, sewer, electrical, …")
    name: str
    polygon: list[Point2]
    severity: Literal["forbidden", "warning", "allowed"] = Field(description="forbidden -- сажать нельзя, warning -- нежелательно, allowed -- разрешённая зона.")
    minDistance: float = Field(description="Ширина охранного коридора зоны, м.")
    message: str
    maxHeight: Optional[float] = Field(default=None, description="Ограничение высоты растений, м (например под ЛЭП).")


class SceneObject(BaseModel):
    """Объект на участке: здание, дерево, кустарник, малая форма, подъезд."""

    id: str
    type: str = Field(description="building, tree, bush, hedge_segment, bench, lamp, trash, entrance, …")
    model: str = Field(description="Путь к 3D-модели или пустая строка.")
    position: Point3
    rotation: float = Field(description="Поворот вокруг вертикальной оси, радианы.")
    scale: float
    metadata: dict = Field(description="species, catalogId, sourceLayer (из чертежа), generated и source (GreenPlan, ассистент), …")


class SceneMeta(BaseModel):
    """Привязка сцены к исходному чертежу."""

    scale: float = Field(description="Метров в единице чертежа.")
    insunits: int = Field(description="$INSUNITS исходного DXF.")
    origin: dict = Field(description="Центр участка в координатах чертежа: {x, y}.")
    buildingCount: int
    pointObjectCount: int
    sourceId: Optional[str] = Field(default=None, description="id исходного чертежа на сервере: экспорт DXF пишет результат поверх него.")


class LawnArea(BaseModel):
    """Газон -- площадью в м², как в ведомости элементов озеленения."""

    id: str
    polygon: list[Point2]
    holes: list[list[Point2]] = Field(default=[], description="Клумбы кустарника внутри газона.")
    area_sqm: float
    status: Literal["new", "existing"] = Field(description="new -- устройство нового газона, existing -- сохраняемый газон из чертежа.")
    kind: str = "Газон обыкновенный"


class Scene(BaseModel):
    """Сцена участка: граница, зоны ограничений, объекты, газон."""

    boundary: Optional[Boundary]
    restrictions: list[RestrictionZone]
    objects: list[SceneObject]
    windows: list[list[Point3]] = Field(default=[], description="Окна фасадов (для 3D).")
    canopies: list[list[Point3]] = Field(default=[], description="Козырьки подъездов (для 3D).")
    curbs: list[list[Point2]] = Field(default=[], description="Бордюры (для 3D).")
    # Отдельно от restrictions: иначе каждое нарушение у дома считалось бы дважды.
    buildingSetbacks: list[RestrictionZone] = Field(default=[], description="Кольца нормативных отступов вокруг зданий, только для отображения.")
    lawns: list[LawnArea] = Field(
        default=[],
        description="Газон: сохраняемый газон чертежа приходит сразу при загрузке, новый добавляет GreenPlan.",
    )
    meta: SceneMeta
