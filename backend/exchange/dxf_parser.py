"""
Мост к parser/parse_dxf.py: парсер лежит отдельной папкой вне пакетов
backend (его запускают и из командной строки), поэтому путь добавляется в
sys.path здесь, в одном месте.

Здесь же объекту с названием вида (metadata.species) назначается позиция
каталога бэкенда (catalogId), а с ней -- 3D-модель и габариты вида, а сцене
загруженного чертежа -- кольца отступов у зданий и сохраняемый газон
(complete_scene).
"""

import sys

from core.building_setbacks import compute_building_setbacks
from core.paths import PARSER_DIR
from core.plant_catalog import catalog_by_id, load_catalog
from core.schemas import Scene
from greenplan.lawn import existing_lawns

if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from parse_dxf import parse_dxf_doc as _parse_dxf_doc  # noqa: E402
from parse_dxf import parse_dxf_file as _parse_dxf_file  # noqa: E402

__all__ = ["complete_scene", "parse_dxf_doc", "parse_dxf_file"]


def _with_catalog_ids(scene: dict) -> dict:
    by_label = {item.label.lower(): item.id for item in load_catalog() if item.category in ("tree", "bush")}
    for obj in scene.get("objects", []):
        metadata = obj.get("metadata") or {}
        species = metadata.get("species")
        if species and "catalogId" not in metadata and species.lower() in by_label:
            metadata["catalogId"] = by_label[species.lower()]
    return scene


def parse_dxf_file(path, *args, **kwargs) -> dict:
    return _with_catalog_ids(_parse_dxf_file(path, *args, **kwargs))


def parse_dxf_doc(doc, *args, **kwargs) -> dict:
    return _with_catalog_ids(_parse_dxf_doc(doc, *args, **kwargs))


def complete_scene(scene: dict) -> dict:
    """То, что сцена получает при загрузке чертежа (DXF и пачки DWG): кольца
    нормативных отступов вокруг зданий и сохраняемый газон со слоёв плана
    покрытий -- чтобы газон из чертежа был виден сразу, до GreenPlan."""
    scene["buildingSetbacks"] = compute_building_setbacks(scene.get("objects", []))
    lawns = existing_lawns(Scene.model_validate(scene), catalog_by_id())
    scene["lawns"] = [lawn.model_dump() for lawn in lawns]
    return scene
