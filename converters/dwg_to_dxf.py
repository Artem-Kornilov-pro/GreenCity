#!/usr/bin/env python3
"""
DWG -> DXF. DWG -- закрытый бинарный формат Autodesk, чистого Python-парсера
для него нет (ezdxf сознательно поддерживает только DXF). Здесь -- тонкая
обёртка над `dwg2dxf` из LibreDWG (открытая, GNU, без лицензионного клик-through
в отличие от официального ODA File Converter):

    macOS:  brew install libredwg
    Linux:  apt install libredwg-tools  (или сборка из исходников)

LibreDWG умеет не всё. Проверено round-trip тестом (DXF -> DWG -> DXF на нашем
собственном файле): LWPOLYLINE, POLYLINE, LINE, CIRCLE, 3DFACE, HATCH, TEXT
проходят без потерь entity-в-entity, а вот 3D MESH (объёмные полигональные
сетки, которыми в этом проекте залиты объёмы зданий) -- пропадают полностью.
Если в DWG есть 3D-меши -- после конвертации ОБЯЗАТЕЛЬНО проверьте их наличие
(см. подсказку в конце вывода этого скрипта). Также могут не распознаться
сложные ACIS-тела и некоторые версии формата/проприетарные типы объектов.
Если конвертация даёт пустой/сильно урезанный DXF на реальном файле от
Мосгеотреста -- следующий шаг: официальный ODA File Converter (бесплатный, но
GUI/лицензионное соглашение, не годится для полной автоматизации без ручного
шага установки): https://www.opendesign.com/guestfiles/oda_file_converter

Использование:
    python3 dwg_to_dxf.py input.dwg output.dxf
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Путь к .dwg")
    ap.add_argument("output", help="Путь к выходному .dxf")
    args = ap.parse_args()

    tool = shutil.which("dwg2dxf")
    if tool is None:
        sys.exit(
            "dwg2dxf не найден в PATH. Установите LibreDWG:\n"
            "  macOS:  brew install libredwg\n"
            "  Linux:  apt install libredwg-tools\n"
            "Если после установки конвертация будет давать пустой/битый DXF на "
            "реальных файлах -- см. ODA File Converter (ссылка в докстринге этого файла)."
        )

    in_path = Path(args.input)
    if not in_path.exists():
        sys.exit(f"Файл не найден: {in_path}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [tool, "-o", str(out_path), str(in_path)],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 or not out_path.exists():
        sys.exit(
            f"dwg2dxf завершился с ошибкой (код {result.returncode}):\n{result.stderr.strip()}\n\n"
            "Если файл читается официальным AutoCAD/DraftSight без проблем, но не "
            "конвертируется здесь -- велика вероятность, что дело в неполной "
            "поддержке конкретных объектов/версии формата в LibreDWG. "
            "В этом случае используйте ODA File Converter."
        )

    print(f"-> {out_path}")
    print("Проверить содержимое: python3 ../parser/parse_dxf.py " + str(out_path) + " --summary")
    print("(если ожидались 3D-объёмы зданий -- проверьте отдельно, что в выводе есть слои с MESH: "
          "LibreDWG в наших тестах не переносит 3D MESH при конвертации через DWG)")


if __name__ == "__main__":
    main()
