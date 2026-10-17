"""
Сборка пачки чертежей в один DXF-документ: каждый .dwg конвертируется через
dwg2dxf (LibreDWG), .dxf читается как есть, затем modelspace и непустые
листы импортируются в общий document ezdxf (ezdxf.addons.importer.Importer).

Внешние ссылки (XREF): файлы комплекта кладутся в общие координаты, но если
чертёж вставляет внешнюю ссылку на другой файл комплекта со сдвигом,
поворотом или масштабом, этот файл сдвигается так же (xref_placements).
Сама вставка внешней ссылки содержимого не даёт -- его даёт файл комплекта.

Частичный успех -- обычный сценарий: проекты приходят пачкой в 20-30 файлов,
и часть из них почти пуста или содержит то, что LibreDWG не читает. Такие
файлы попадают в BatchConversionResult.failed, остальные собираются.

Файлы разбираются параллельно в отдельных процессах (workers > 1): каждый
конвертирует и читает свой файл и оставляет только то, что прочитает парсер
(dxf_parsing.drawing.worth_parsing: распознанные слои с учётом синонимов и
назначений пользователя, вставки блоков с таким содержимым, тексты); главный
процесс склеивает компактные документы в исходном порядке. Сколько сущностей
было на каждом слое до отбора, тоже собирается -- для сводки слоёв, чтобы
пользователь видел и отброшенные слои и мог назначить им тип.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Optional

import ezdxf
import ezdxf.recover
from ezdxf.addons.importer import Importer
from ezdxf.math import Matrix44

import exchange.dxf_parser  # noqa: F401 -- добавляет parser/ в sys.path
from dxf_parsing.drawing import ParsedContentFilter
from dxf_parsing.layer_names import LayerResolver
from exchange.dxf_parser import read_dxf, reread_in_real_encoding

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
    # Слои исходных файлов до отбора: {имя слоя: число сущностей}.
    layer_counts: dict[str, int] = field(default_factory=dict)
    # Файлы, сдвинутые по вставке внешней ссылки: {имя файла: кто вставил}.
    placed_by_xref: dict[str, str] = field(default_factory=dict)


def _convert_one(tool: str, dwg_path: Path, dxf_path: Path) -> None:
    # Без text=True: dwg2dxf пишет в stderr имена слоёв в кодировке файла
    # (часто cp1251), и декодирование как UTF-8 уронило бы subprocess.run.
    # Текст ошибки декодируем сами, с errors="replace".
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
    """dwg2dxf иногда пишет текст вперемешку в UTF-8 и cp1251, и строгий
    ezdxf.readfile падает с UnicodeDecodeError. ezdxf.recover.readfile такое
    читает, но медленнее -- поэтому он запасной вариант."""
    try:
        # dwg2dxf пишет имена в UTF-8 при объявленной ANSI_1252: такой чертёж
        # перечитывается в настоящей кодировке, иначе ни один русский слой не
        # распознаётся.
        return reread_in_real_encoding(dxf_path, ezdxf.readfile(dxf_path))
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
    (XML спецификаций в объектах чертежа):

    1. Перевод строки внутри значения сдвигает пары «код группы / значение».
       Строка на месте кода группы, которая не число, -- продолжение значения;
       она выбрасывается.
    2. Битое юникод-экранирование без четырёх hex-цифр удаляется.

    Геометрию это не трогает. Возвращает число исправлений."""
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


def _import_all_layouts(
    source_doc: ezdxf.document.Drawing,
    target_doc: ezdxf.document.Drawing,
    layer_map: Optional[dict[str, str]] = None,
    layer_counts: Optional[Counter] = None,
) -> None:
    """modelspace и все непустые paperspace-листы источника -- в modelspace
    цели, только то, что читает парсер (с назначениями пользователя
    layer_map). layer_counts, если передан, получает число сущностей по слоям
    источника до отбора."""
    keep = ParsedContentFilter(LayerResolver(layer_map))
    importer = Importer(source_doc, target_doc)
    layouts = [source_doc.modelspace()] + [source_doc.layouts.get(name) for name in source_doc.layouts.names() if name != "Model"]
    for layout in layouts:
        entities = list(layout)
        if layer_counts is not None:
            layer_counts.update(e.dxf.layer for e in entities if e.dxftype() not in ("TEXT", "MTEXT"))
        importer.import_entities([e for e in entities if keep(e)])
    importer.finalize()


def _xref_key(path_or_name: str) -> str:
    """Имя файла внешней ссылки без папки и расширения, в нижнем регистре:
    путь в чертеже -- виндовый и часто абсолютный («C:\\Проект\\tp.dwg»)."""
    return PureWindowsPath(path_or_name).stem.lower()


def xref_placements(doc: ezdxf.document.Drawing) -> dict[str, list[float]]:
    """{имя файла внешней ссылки: матрица её вставки (16 чисел)} -- по
    первой вставке каждой ссылки в пространстве модели."""
    found: dict[str, list[float]] = {}
    for insert in doc.modelspace().query("INSERT"):
        block = doc.blocks.get(insert.dxf.name)
        if block is None or not block.block_record.is_xref:
            continue
        matrix = list(insert.matrix44())
        for name in (block.block.dxf.get("xref_path") or "", insert.dxf.name):
            if name:
                found.setdefault(_xref_key(name), matrix)
    return found


def _is_identity(matrix: list[float]) -> bool:
    return all(abs(a - b) < 1e-9 for a, b in zip(matrix, Matrix44(), strict=True))


def _place(doc: ezdxf.document.Drawing, matrix: list[float]) -> None:
    """Сдвинуть, повернуть, отмасштабировать пространство модели и листы так,
    как стоит вставка внешней ссылки на этот файл."""
    m = Matrix44(matrix)
    for layout in doc.layouts:
        for entity in layout:
            try:
                entity.transform(m)
            except (NotImplementedError, ezdxf.DXFError, ValueError, ArithmeticError):
                # Сущность без поддержки преобразования (прокси, ACIS) --
                # парсер её всё равно не читает.
                continue


def _read_source(path: Path, intermediate: Path) -> ezdxf.document.Drawing:
    if path.suffix.lower() == ".dxf":
        return read_dxf(path)
    _convert_one(dwg2dxf_path(), path, intermediate)
    return _read_converted_dxf(intermediate)


def _extract_compact(
    dwg_path: str, work_dir: str, layer_map: Optional[dict[str, str]] = None
) -> tuple[str, str | None, str | None, dict[str, int], dict[str, list[float]]]:
    """Рабочий процесс: чтение файла (.dwg -- через dwg2dxf), отбор нужного
    парсеру -- в компактный DXF. (имя файла, путь к компактному DXF или None,
    ошибка, сущностей по слоям до отбора, вставки внешних ссылок)."""
    src, work = Path(dwg_path), Path(work_dir)
    dxf_tmp = work / (src.stem + ".dxf")
    counts: Counter = Counter()
    try:
        source_doc = _read_source(src, dxf_tmp)
    except Exception as e:
        if src.suffix.lower() != ".dxf":
            dxf_tmp.unlink(missing_ok=True)
        return src.name, None, str(e), {}, {}
    try:
        placements = xref_placements(source_doc)
        compact = ezdxf.new(dxfversion=DXF_VERSION)
        _import_all_layouts(source_doc, compact, layer_map, counts)
        compact_path = work / (src.stem + ".compact.dxf")
        compact.saveas(compact_path)
    except Exception as e:
        return src.name, None, f"ошибка импорта в общий документ: {e}", {}, {}
    finally:
        if src.suffix.lower() != ".dxf":
            dxf_tmp.unlink(missing_ok=True)  # полный DXF больше не нужен -- место на диске
    return src.name, str(compact_path), None, dict(counts), placements


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


def merge_dwg_files(
    dwg_paths: list[Path], intermediate_dir: Path, workers: int = 1, layer_map: Optional[dict[str, str]] = None
) -> BatchConversionResult:
    """Читает каждый файл из `dwg_paths` (.dwg -- через dwg2dxf, .dxf -- как
    есть) и сливает в один ezdxf-документ (только то, что читает парсер;
    layer_map -- назначения слоёв пользователем). workers > 1 -- файлы
    обрабатываются параллельно в отдельных процессах (см. докстринг модуля).
    Поднимает `Dwg2DxfNotFound`, если в пачке есть .dwg, а сам конвертер не
    установлен -- эта ошибка останавливает весь батч, в отличие от ошибок на
    отдельных файлах."""
    if any(p.suffix.lower() == ".dwg" for p in dwg_paths):
        dwg2dxf_path()
    target_doc = ezdxf.new(dxfversion=DXF_VERSION)
    result = BatchConversionResult(doc=target_doc)
    dwg_paths = _skip_identical_files(dwg_paths, result)
    n = len(dwg_paths)
    args = ([str(p) for p in dwg_paths], [str(intermediate_dir)] * n, [layer_map] * n)
    if workers > 1 and n > 1:
        with ProcessPoolExecutor(max_workers=min(workers, n)) as pool:
            extracted = list(pool.map(_extract_compact, *args))
    else:
        extracted = list(map(_extract_compact, *args))

    # Вставки внешних ссылок из всех файлов: файл, на который ссылается
    # другой файл комплекта, кладётся так, как стоит вставка.
    placements: dict[str, tuple[str, list[float]]] = {}
    for name, _path, _error, _counts, file_placements in extracted:
        for key, matrix in file_placements.items():
            if key != _xref_key(name):
                placements.setdefault(key, (name, matrix))

    counts: Counter = Counter()
    for name, compact_path, error, file_counts, _placements in extracted:
        if compact_path is None:
            result.failed[name] = error or "неизвестная ошибка"
            continue
        try:
            compact = ezdxf.readfile(compact_path)
            placed = placements.get(_xref_key(name))
            if placed is not None and not _is_identity(placed[1]):
                _place(compact, placed[1])
                result.placed_by_xref[name] = placed[0]
            _import_all_layouts(compact, target_doc, layer_map)
        except Exception as e:
            result.failed[name] = f"ошибка импорта в общий документ: {e}"
            continue
        finally:
            Path(compact_path).unlink(missing_ok=True)
        counts.update(file_counts)
        result.converted.append(name)
    result.layer_counts = dict(counts)
    return result
