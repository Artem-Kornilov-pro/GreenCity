"""
Мост к parser/parse_dxf.py. Парсер живёт отдельной папкой parser/ (его же
запускают из командной строки: python3 parser/parse_dxf.py file.dxf), вне
пакетов backend, поэтому путь к нему добавляется в sys.path -- здесь, в одном
месте, а не в каждом модуле, которому нужен разбор DXF.

Здесь же -- то, чего парсер знать не может: позиция каталога бэкенда. Слой с
названием вида ("ЛИПА МЕЛКОЛИСТНАЯ") парсер помечает metadata.species; по
нему объект получает catalogId, а с ним -- 3D-модель и габариты вида (без
этого любое дерево из DXF рисовалось одной моделью по умолчанию).
"""

import sys

from core.paths import PARSER_DIR
from core.plant_catalog import load_catalog

if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from parse_dxf import parse_dxf_doc as _parse_dxf_doc  # noqa: E402
from parse_dxf import parse_dxf_file as _parse_dxf_file  # noqa: E402

__all__ = ["parse_dxf_doc", "parse_dxf_file"]


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
