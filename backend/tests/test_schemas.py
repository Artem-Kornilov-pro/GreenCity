"""schemas.Scene и связанные модели -- в основном чистая структура данных
(Pydantic валидация), но пара мест стоит проверить явно: необязательные поля
со значением по умолчанию и то, что boundary может отсутствовать."""

import pytest
from pydantic import ValidationError
from schemas import Boundary, Point2, Point3, RestrictionZone, Scene, SceneMeta, SceneObject


def _meta() -> SceneMeta:
    return SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0)


def test_scene_boundary_is_optional():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert scene.boundary is None


def test_scene_windows_and_canopies_default_to_empty_list():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert scene.windows == []
    assert scene.canopies == []


def test_restriction_zone_severity_is_restricted_to_known_values():
    zone = RestrictionZone(
        id="z1", type="building", name="Дом", polygon=[Point2(x=0, z=0)], severity="forbidden", minDistance=5.0, message="msg"
    )
    assert zone.severity == "forbidden"
    with pytest.raises(ValidationError):
        RestrictionZone(
            id="z1", type="building", name="Дом", polygon=[Point2(x=0, z=0)], severity="danger", minDistance=5.0, message="msg"
        )


def test_restriction_zone_max_height_defaults_to_none():
    zone = RestrictionZone(
        id="z1", type="building", name="Дом", polygon=[], severity="allowed", minDistance=0.0, message="msg"
    )
    assert zone.maxHeight is None


def test_scene_object_requires_all_core_fields():
    obj = SceneObject(id="tree_1", type="tree", model="/models/tree.glb", position=Point3(x=1, y=0, z=2), rotation=0.0, scale=1.0, metadata={})
    assert obj.position.x == 1
    with pytest.raises(ValidationError):
        SceneObject(id="tree_1", type="tree", model="/models/tree.glb", position=Point3(x=1, y=0, z=2), rotation=0.0, metadata={})


def test_scene_round_trips_through_json():
    boundary = Boundary(polygon=[Point2(x=0, z=0), Point2(x=10, z=0), Point2(x=10, z=10)], sourceLayer="TERRITORY_BOUNDARY")
    scene = Scene(boundary=boundary, restrictions=[], objects=[], meta=_meta())
    dumped = scene.model_dump(mode="json")
    restored = Scene.model_validate(dumped)
    assert restored.boundary.sourceLayer == "TERRITORY_BOUNDARY"
    assert len(restored.boundary.polygon) == 3
