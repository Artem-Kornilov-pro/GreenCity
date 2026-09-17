from __future__ import annotations

import glob
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "parser")]


# См. тот же комментарий в backend/tests/conftest.py: исходные шесть участков
# переехали в locations/location_old/, а короткий путь теперь указывал бы на
# другие, настоящие локации с пересекающейся нумерацией.
def _location_path(number: int) -> str:
    matches = glob.glob(str(ROOT / "locations" / "location_old" / f"{number:02d}_*" / "*.dxf"))
    if not matches:
        raise FileNotFoundError(f"нет фикстуры locations/location_old/{number:02d}_*/*.dxf")
    return matches[0]


@pytest.fixture(scope="session")
def location_paths():
    return {n: _location_path(n) for n in range(1, 7)}


@pytest.fixture
def empty_doc():
    """Пустой DXF-документ (R2010, метры) -- на нём удобно собирать сущности
    руками для проверки конкретной ветки парсера, не завися от содержимого
    настоящих файлов locations/."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    return doc
