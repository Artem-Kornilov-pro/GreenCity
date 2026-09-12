"""Общие билдеры Scene/RestrictionZone/SceneObject для тестов, которым нужна
геометрия под полный контроль (placement.py, courtyard_design.py,
greenery_generator.py, export_dxf.py) -- в отличие от location_scene из
conftest.py, где геометрия настоящая, но неудобная для точных ожиданий в
духе "эта точка обязана быть свободна, а вот эта -- нет"."""

from __future__ import annotations

from schemas import Boundary, Point2, Point3, RestrictionZone, Scene, SceneMeta, SceneObject


def rect_points(x0: float, z0: float, x1: float, z1: float) -> list[Point2]:
    return [Point2(x=x0, z=z0), Point2(x=x1, z=z0), Point2(x=x1, z=z1), Point2(x=x0, z=z1)]


def make_boundary(x0: float = -50, z0: float = -50, x1: float = 50, z1: float = 50, source_layer: str = "TERRITORY_BOUNDARY") -> Boundary:
    return Boundary(polygon=rect_points(x0, z0, x1, z1), sourceLayer=source_layer)


def make_zone(
    id: str = "z1",
    type: str = "building",
    name: str = "BUILDING_1",
    severity: str = "forbidden",
    min_distance: float = 1.5,
    message: str = "zone",
    polygon: list[Point2] | None = None,
    max_height: float | None = None,
) -> RestrictionZone:
    return RestrictionZone(
        id=id,
        type=type,
        name=name,
        polygon=polygon if polygon is not None else rect_points(-5, -5, 5, 5),
        severity=severity,
        minDistance=min_distance,
        message=message,
        maxHeight=max_height,
    )


def make_object(
    id: str,
    type: str,
    x: float,
    z: float,
    y: float = 0.0,
    rotation: float = 0.0,
    scale: float = 1.0,
    model: str = "/models/x.glb",
    metadata: dict | None = None,
) -> SceneObject:
    return SceneObject(
        id=id,
        type=type,
        model=model,
        position=Point3(x=x, y=y, z=z),
        rotation=rotation,
        scale=scale,
        metadata=metadata or {},
    )


_UNSET = object()


def make_scene(
    boundary=_UNSET,
    restrictions: list[RestrictionZone] | None = None,
    objects: list[SceneObject] | None = None,
) -> Scene:
    """boundary не передан вовсе -> дефолтный участок 100x100 м.
    boundary=None ЯВНО -> сцена без границы вовсе (тест этого сценария:
    Placer.site тогда строится из envelope зон, либо остаётся None)."""
    return Scene(
        boundary=make_boundary() if boundary is _UNSET else boundary,
        restrictions=restrictions or [],
        objects=objects or [],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )
