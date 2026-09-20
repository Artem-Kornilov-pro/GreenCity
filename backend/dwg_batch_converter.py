"""
Батч-конвертация нескольких .dwg в один .dxf (issue #50): тонкая обвязка над
`dwg2dxf` (LibreDWG, та же обёртка, что и в CLI-скрипте `converters/
dwg_to_dxf.py`) + `ezdxf.addons.importer.Importer`, повторяющая подход
рабочего прототипа (`Converter/main.py`, вне репозитория) -- конвертирует
каждый .dwg по отдельности во временный .dxf, затем импортирует modelspace и
все непустые paperspace-лейауты каждого файла в один общий ezdxf-документ.

Частичный успех -- ожидаемый сценарий, а не ошибка: реальные DWG-проекты
приходят россыпью в 20-30 файлов (генплан-сборка + Xrefs), и часть из них
типично либо почти пустая (сборочный файл с нерезолвленными xref-ссылками),
либо содержит объекты, которые LibreDWG не умеет читать (3D MESH, сложные
ACIS-тела, некоторые версии формата -- см. докстринг `converters/
dwg_to_dxf.py`). Поэтому файлы, не поддавшиеся конвертации или импорту, не
прерывают весь батч -- они попадают в `BatchConversionResult.failed`, а
успешные продолжают собираться в общий документ.

Обработка файлов НАМЕРЕННО последовательная, не параллельная -- это не
недосмотр, а результат прямого замера (issue #50 follow-up, "слишком долго
загружается проект"). Профилирование на реальных файлах Мосгеотреста
показало: сам `dwg2dxf` (внешний процесс) быстрый, 0.5-0.8с/файл, а узкое
место -- разбор итогового DXF через `ezdxf.readfile`/`recover` (3-4.5с/файл),
чистый Python, держит GIL. Пул ПОТОКОВ поэтому не ускоряет вообще (GIL не
даёт двум потокам разбирать DXF одновременно, замерено -- без выигрыша).
Пул ПРОЦЕССОВ в изолированном скрипте (вне HTTP-сервера) на том же батче из
5 файлов действительно ускорял разбор в ~1.8 раза (~27с -> ~14-15с) -- но
тот же самый код, вызванный внутри РЕАЛЬНОГО процесса backend (уже
загруженный retrieval-корпус, клиенты Mongo/Redis, потоки uvicorn), давал
~40-43с что для fork, что для spawn -- то есть НИКАКОГО выигрыша: разница
между "чистым" процессом и "тяжёлым" процессом backend съедает всю выгоду.
Оставлено последовательным, чтобы не добавлять сложность и риск (всплеск
памяти от нескольких одновременных ezdxf-документов, fork в
многопоточном процессе) без реальной пользы.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import ezdxf.recover
from ezdxf.addons.importer import Importer

DXF_VERSION = "R2010"


class Dwg2DxfNotFound(RuntimeError):
    """dwg2dxf (LibreDWG) не найден в PATH."""


def dwg2dxf_path() -> str:
    tool = shutil.which("dwg2dxf")
    if tool is None:
        raise Dwg2DxfNotFound(
            "dwg2dxf не найден в PATH. Установите LibreDWG (macOS: brew install "
            "libredwg, Linux: apt install libredwg-tools)."
        )
    return tool


@dataclass
class BatchConversionResult:
    doc: ezdxf.document.Drawing
    converted: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _convert_one(tool: str, dwg_path: Path, dxf_path: Path) -> None:
    # capture_output БЕЗ text=True -- принципиально: dwg2dxf печатает в stderr
    # предупреждения об "Unstable Class object" с именами слоёв/блоков прямо
    # из внутренних строковых таблиц DWG, в кодировке исходного файла (не
    # обязательно UTF-8, реальные файлы Мосгеотреста часто в cp1251).
    # subprocess с text=True декодирует stdout/stderr как UTF-8 сам, ДО того
    # как код здесь вообще получает управление -- на таком выводе это падает
    # с UnicodeDecodeError прямо внутри subprocess.run, независимо от того,
    # прочитался ли итоговый DXF-файл вообще (сам файл при этом мог быть
    # написан корректно). Декодируем поэтому вручную и терпимо (errors=
    # "replace") только когда нужно показать текст ошибки человеку.
    result = subprocess.run(
        [tool, "-o", str(dxf_path), str(dwg_path)],
        capture_output=True,
    )
    if result.returncode != 0 or not dxf_path.exists():
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        detail = stderr or stdout or f"код возврата {result.returncode}"
        raise RuntimeError(detail)


def _read_converted_dxf(dxf_path: Path) -> ezdxf.document.Drawing:
    """dwg2dxf на реальных файлах Мосгеотреста иногда пишет несогласованную
    кодировку текста: заголовок `$DWGCODEPAGE` объявляет однобайтовый codepage
    (например `ANSI_1251`), но часть текстовых строк на деле в UTF-8, а часть
    -- в сыром cp1251, вперемешку в одном файле. Строгий `ezdxf.readfile`
    падает с `UnicodeDecodeError` на первой же такой строке. `ezdxf.recover.
    readfile` умеет именно это чинить (свой более терпимый разбор кодировки +
    авто-audit структуры) -- используем его как fallback, а не по умолчанию,
    потому что он заметно медленнее на больших файлах."""
    try:
        return ezdxf.readfile(dxf_path)
    except (UnicodeDecodeError, ezdxf.DXFStructureError):
        doc, _auditor = ezdxf.recover.readfile(dxf_path)
        return doc


def _import_all_layouts(source_doc: ezdxf.document.Drawing, target_doc: ezdxf.document.Drawing) -> None:
    importer = Importer(source_doc, target_doc)
    if len(source_doc.modelspace()):
        importer.import_entities(source_doc.modelspace())
    for name in source_doc.layouts.names():
        if name == "Model":
            continue
        layout = source_doc.layouts.get(name)
        if len(layout):
            importer.import_entities(layout)
    importer.finalize()


def merge_dwg_files(dwg_paths: list[Path], intermediate_dir: Path) -> BatchConversionResult:
    """Конвертирует каждый файл из `dwg_paths` во временный .dxf внутри
    `intermediate_dir` и сливает результаты в один ezdxf-документ. Поднимает
    `Dwg2DxfNotFound`, если сам конвертер не установлен -- эта ошибка
    останавливает весь батч, в отличие от ошибок на отдельных файлах."""
    tool = dwg2dxf_path()
    target_doc = ezdxf.new(dxfversion=DXF_VERSION)
    result = BatchConversionResult(doc=target_doc)

    for dwg_path in dwg_paths:
        dxf_tmp = intermediate_dir / (dwg_path.stem + ".dxf")
        try:
            _convert_one(tool, dwg_path, dxf_tmp)
            source_doc = _read_converted_dxf(dxf_tmp)
        except Exception as e:
            result.failed[dwg_path.name] = str(e)
            continue

        try:
            _import_all_layouts(source_doc, target_doc)
        except Exception as e:
            result.failed[dwg_path.name] = f"ошибка импорта в общий документ: {e}"
            continue

        result.converted.append(dwg_path.name)

    return result
