"""
Проверка выходного DXF без CAD: исходные сущности на месте и не изменены,
результат -- только на слоях NEW_* / USER_*, у каждой сущности результата есть
XDATA GREENCITY, и (если передан файл объяснений) каждая посадка из него стоит
в DXF в тех же координатах.

    .venv/bin/python tools/check_export.py <исходный.dxf> <результат.dxf> [объяснения.json]

Код возврата 0 -- все проверки пройдены.
"""

import json
import sys
from collections import Counter

import ezdxf

APPID = "GREENCITY"
RESULT_PREFIXES = ("NEW_", "USER_")
COORD_TOLERANCE = 1e-3


def _signature(entity) -> tuple:
    """Тип, слой и координаты определяющих точек -- чтобы заметить сдвиг."""
    attribs = entity.dxf.all_existing_dxf_attribs()
    points = tuple(
        (key, tuple(round(c, 6) for c in value)) for key, value in sorted(attribs.items()) if hasattr(value, "x")
    )
    return entity.dxftype(), entity.dxf.layer, points


def main(source_path: str, result_path: str, explanations_path: str | None = None) -> int:
    source = ezdxf.readfile(source_path)
    result = ezdxf.readfile(result_path)
    ok = True

    source_entities = {e.dxf.handle: _signature(e) for e in source.modelspace()}
    result_by_handle = {e.dxf.handle: e for e in result.modelspace()}
    missing = [h for h in source_entities if h not in result_by_handle]
    changed = [h for h, sig in source_entities.items() if h in result_by_handle and _signature(result_by_handle[h]) != sig]
    print(f"Исходных сущностей: {len(source_entities)}; пропало: {len(missing)}; изменено: {len(changed)}")
    ok &= not missing and not changed

    added = [e for h, e in result_by_handle.items() if h not in source_entities]
    outside = [e for e in added if not e.dxf.layer.upper().startswith(RESULT_PREFIXES)]
    untagged = [e for e in added if not e.has_xdata(APPID)]
    print(f"Добавлено сущностей: {len(added)}; вне слоёв NEW_*/USER_*: {len(outside)}; без XDATA {APPID}: {len(untagged)}")
    ok &= not outside and not untagged
    for layer, count in sorted(Counter(e.dxf.layer for e in added).items()):
        print(f"  {layer}: {count}")

    if explanations_path:
        with open(explanations_path, encoding="utf-8") as f:
            plants = json.load(f)["plants"]
        points = {}
        for e in added:
            if e.dxftype() == "POINT":
                obj_id = e.get_xdata(APPID)[0].value
                points[obj_id] = (e.dxf.location.x, e.dxf.location.y, e.dxf.layer)
        mismatched = []
        for plant in plants:
            point = points.get(plant["id"])
            if (
                point is None
                or abs(point[0] - plant["dxf_x"]) > COORD_TOLERANCE
                or abs(point[1] - plant["dxf_y"]) > COORD_TOLERANCE
                or point[2] != plant["dxf_layer"]
            ):
                mismatched.append(plant["id"])
        print(f"Посадок в файле объяснений: {len(plants)}; не найдено в DXF или не совпало: {len(mismatched)}")
        ok &= not mismatched

    print("ПРОВЕРКА ПРОЙДЕНА" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
