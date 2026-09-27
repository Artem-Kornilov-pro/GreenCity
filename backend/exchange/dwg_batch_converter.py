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

Файлы обрабатываются параллельно, в отдельных процессах (workers > 1).
Узкое место -- чтение DXF через ezdxf (3-6 с на файл, чистый Python, одно
ядро): замер на реальной пачке Мосгеотреста (5 файлов DWG, ~200 МБ DXF) --
dwg2dxf 3 с, чтение DXF 15 с, склейка 3 с, разбор 7 с. Раньше пул процессов
здесь не помогал, потому что запускался из тяжёлого процесса uvicorn; теперь
разбор пачки идёт в отдельном лёгком процессе (exchange/dwg_job.py), и пул из
него работает. Каждый рабочий процесс конвертирует свой файл, читает его и
оставляет только слои, которые парсер вообще читает (dxf_parsing.rules.
layer_is_parsed, плюс тексты -- подписи зданий), в компактном DXF; главный
процесс склеивает компактные файлы в исходном порядке. Сцена после разбора
та же до байта (сверено на реальной пачке), а вышло 18,5 с вместо 28,5 и пик
памяти главного процесса 0,75 ГБ вместо 1,6 (рабочий процесс -- ~0,5 ГБ).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import ezdxf.recover
from ezdxf.addons.importer import Importer

import exchange.dxf_parser  # noqa: F401 -- добавляет parser/ в sys.path
from dxf_parsing.rules import layer_is_parsed

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
        pass
    try:
        doc, _auditor = ezdxf.recover.readfile(dxf_path)
        return doc
    except Exception:
        # Третья ступень -- см. _repair_dxf_text.
        cleaned = dxf_path.with_suffix(".clean.dxf")
        try:
            if _repair_dxf_text(dxf_path, cleaned) == 0:
                raise
            doc, _auditor = ezdxf.recover.readfile(cleaned)
            return doc
        finally:
            cleaned.unlink(missing_ok=True)


# Юникод-экранирование DXF -- обратная косая, "U+" и ровно четыре hex-цифры.
# Без цифр ezdxf раскодирует его как int("", 16) и падает.
_BROKEN_UNICODE_ESCAPE = re.compile(rb"\x5cU\+(?![0-9A-Fa-f]{4})")


def _repair_dxf_text(src: Path, dst: Path) -> int:
    """Чинит два дефекта, которые dwg2dxf пишет в многострочные значения
    (XML спецификации в объектах чертежа: `<Item name="ПНД…"
    sendInSpecification="True" />`). Реальный случай -- 3 из 4 файлов пачки
    улицы Берзарина: ни строгий ezdxf.readfile, ни recover их не читали.

    1. Значение с переводами строк: DXF -- строгие пары "код группы /
       значение", и каждая лишняя строка сдвигает пары ("Invalid group
       code"). Строка на месте кода группы, которая не число, -- продолжение
       предыдущего значения; выбрасываем её, пары восстанавливаются.
    2. Битое юникод-экранирование без четырёх hex-цифр ("invalid literal for
       int() with base 16: ''") -- убираем его.

    Геометрию это не трогает: страдает только текст служебных строк.
    Возвращает число исправлений (0 -- чинить было нечего)."""
    fixes = 0
    expect_code = True
    with src.open("rb") as fin, dst.open("wb") as fout:
        for line in fin:
            if expect_code:
                if line.strip().lstrip(b"-").isdigit():
                    fout.write(line)
                    expect_code = False
                else:
                    fixes += 1
            else:
                line, n = _BROKEN_UNICODE_ESCAPE.subn(b"", line)
                fixes += n
                fout.write(line)
                expect_code = True
    return fixes


def _parsed_entities(layout) -> list:
    """Сущности, которые парсер читает: со слоёв, узнаваемых его правилами,
    и все тексты (подписи зданий парсер ищет на любом слое)."""
    return [e for e in layout if e.dxftype() in ("TEXT", "MTEXT") or layer_is_parsed(e.dxf.layer)]


def _import_all_layouts(source_doc: ezdxf.document.Drawing, target_doc: ezdxf.document.Drawing) -> None:
    """modelspace и все непустые paperspace-листы источника -- в modelspace
    цели, только то, что читает парсер."""
    importer = Importer(source_doc, target_doc)
    importer.import_entities(_parsed_entities(source_doc.modelspace()))
    for name in source_doc.layouts.names():
        if name == "Model":
            continue
        importer.import_entities(_parsed_entities(source_doc.layouts.get(name)))
    importer.finalize()


def _extract_compact(dwg_path: str, work_dir: str) -> tuple[str, str | None, str | None]:
    """Рабочий процесс: dwg2dxf, чтение, отбор нужного парсеру -- в
    компактный DXF. (имя файла, путь к компактному DXF или None, ошибка)."""
    dwg, work = Path(dwg_path), Path(work_dir)
    dxf_tmp = work / (dwg.stem + ".dxf")
    try:
        _convert_one(dwg2dxf_path(), dwg, dxf_tmp)
        source_doc = _read_converted_dxf(dxf_tmp)
    except Exception as e:
        return dwg.name, None, str(e)
    try:
        compact = ezdxf.new(dxfversion=DXF_VERSION)
        _import_all_layouts(source_doc, compact)
        compact_path = work / (dwg.stem + ".compact.dxf")
        compact.saveas(compact_path)
    except Exception as e:
        return dwg.name, None, f"ошибка импорта в общий документ: {e}"
    finally:
        dxf_tmp.unlink(missing_ok=True)  # полный DXF больше не нужен -- место на диске
    return dwg.name, str(compact_path), None


def _skip_identical_files(dwg_paths: list[Path], result: BatchConversionResult) -> list[Path]:
    """Побайтно одинаковые файлы -- один раз. Реальная пачка их содержит (на
    Харьковской "АПОТ" и "улица ГП" -- один и тот же файл под двумя именами), и
    всё их содержимое попадало в сцену дважды. Пропущенный -- в failed с
    понятной причиной, он уйдёт пользователю в dwgConversionWarnings."""
    seen: dict[str, str] = {}
    unique = []
    for path in dwg_paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            result.failed[path.name] = f"совпадает с {seen[digest]} -- пропущен как дубль"
            continue
        seen[digest] = path.name
        unique.append(path)
    return unique


def merge_dwg_files(dwg_paths: list[Path], intermediate_dir: Path, workers: int = 1) -> BatchConversionResult:
    """Конвертирует каждый файл из `dwg_paths` и сливает в один ezdxf-документ
    (только то, что читает парсер). workers > 1 -- файлы обрабатываются
    параллельно в отдельных процессах (см. докстринг модуля). Поднимает
    `Dwg2DxfNotFound`, если сам конвертер не установлен -- эта ошибка
    останавливает весь батч, в отличие от ошибок на отдельных файлах."""
    tool = dwg2dxf_path()
    target_doc = ezdxf.new(dxfversion=DXF_VERSION)
    result = BatchConversionResult(doc=target_doc)
    dwg_paths = _skip_identical_files(dwg_paths, result)

    if workers > 1 and len(dwg_paths) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(dwg_paths))) as pool:
            extracted = list(pool.map(_extract_compact, [str(p) for p in dwg_paths], [str(intermediate_dir)] * len(dwg_paths)))
        for name, compact_path, error in extracted:
            if compact_path is None:
                result.failed[name] = error or "неизвестная ошибка"
                continue
            try:
                _import_all_layouts(ezdxf.readfile(compact_path), target_doc)
            except Exception as e:
                result.failed[name] = f"ошибка импорта в общий документ: {e}"
                continue
            result.converted.append(name)
        return result

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
