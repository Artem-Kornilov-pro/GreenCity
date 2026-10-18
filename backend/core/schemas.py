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
    layerKind: Optional[str] = Field(
        default=None, description="Класс слоя, с которого взята зона (GET /api/layer-kinds): газон, парковка… -- не зависит от имени слоя."
    )
    voltageKv: Optional[float] = Field(
        default=None,
        description="Класс напряжения воздушной ЛЭП, кВ: из имени слоя («ВЛ 10кВ») или из параметров GreenPlan; не задан -- 10 кВ.",
    )


class SceneObject(BaseModel):
    """Объект на участке: здание, дерево, кустарник, малая форма, подъезд."""

    id: str
    type: str = Field(description="building, tree, bush, hedge_segment, bench, lamp, trash, entrance, …")
    model: str = Field(description="Путь к 3D-модели или пустая строка.")
    position: Point3
    rotation: float = Field(description="Поворот вокруг вертикальной оси, радианы.")
    scale: float
    metadata: dict = Field(
        description="species, catalogId, sourceLayer (из чертежа), generated и source (GreenPlan, ассистент), "
        "offsite (из чертежа, за границей участка), …"
    )


def is_offsite(obj: SceneObject) -> bool:
    """Объект из чертежа за границей участка (parse_dxf._mark_offsite_points):
    невидимое ограничение -- расстановка соблюдает норму до него, но в
    редакторе, отчётах, проверке нарушений и правке текстом его нет."""
    return bool(obj.metadata.get("offsite"))


class LayerInfo(BaseModel):
    """Слой чертежа в сводке распознавания."""

    name: str
    displayName: Optional[str] = Field(default=None, description="Имя слоя без кракозябр, если в файле оно в другой кодировке.")
    entities: int = Field(description="Сколько на слое сущностей (без текстов).")
    kind: Optional[str] = Field(default=None, description="Класс слоя (GET /api/layer-kinds) или null.")
    label: Optional[str] = Field(default=None, description="Что из слоя взято, по-русски; null -- слой не распознан и не учтён.")
    source: Optional[str] = Field(
        default=None, description="Как распознан: override (назначен пользователем), name, normalized, synonym, content."
    )


class BoundaryCandidate(BaseModel):
    """Замкнутый контур чертежа, который может быть границей участка."""

    id: int
    layer: str
    area: float = Field(description="Площадь, м².")
    points: list[list[float]] = Field(description="Вершины в координатах чертежа.")


class SceneMeta(BaseModel):
    """Привязка сцены к исходному чертежу."""

    scale: float = Field(description="Метров в единице чертежа.")
    insunits: int = Field(description="$INSUNITS исходного DXF.")
    origin: dict = Field(description="Центр участка в координатах чертежа: {x, y}.")
    buildingCount: int
    pointObjectCount: int
    sourceId: Optional[str] = Field(default=None, description="id исходного чертежа на сервере: экспорт DXF пишет результат поверх него.")
    sourceSha256: Optional[str] = Field(
        default=None, description="SHA-256 исходного чертежа (у комплекта -- имён и содержимого файлов в порядке загрузки)."
    )
    layers: list[LayerInfo] = Field(default=[], description="Слои чертежа: что из каждого взято при разборе.")
    layerMap: dict[str, str] = Field(default={}, description="Назначения слоёв пользователем {имя слоя: класс}, с которыми разобран чертёж.")
    warnings: list[str] = Field(default=[], description="Что разбор чертежа сделал сам и что стоит проверить.")
    boundaryCandidates: list[BoundaryCandidate] = Field(
        default=[], description="Слоя границы нет: замкнутые контуры, из которых пользователь может выбрать границу участка."
    )
    boundaryOverride: Optional[list[list[float]]] = Field(
        default=None, description="Граница участка, выбранная пользователем, в координатах чертежа: [[x, y], ...]."
    )
    geo: Optional[dict] = Field(
        default=None,
        description="Привязка к карте (core/geo.py): {crs, assumed, center: {lat, lon}, toScene: 6 чисел} -- если координаты похожи на МСК.",
    )


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
