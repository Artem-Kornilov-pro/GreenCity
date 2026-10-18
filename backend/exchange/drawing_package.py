"""
Комплект чертежей объекта, как его присылает заказчик: папка или zip-архив
с несколькими DWG/DXF -- топосъёмка (tp), подземные сети (up), рамки и
граница заказа (brd), генплан, который тянет их внешними ссылками (XREF).

Здесь -- безопасная распаковка архивов и выбор файлов, которые пойдут в
сборку (exchange/dwg_batch_converter.py). Сборка кладёт файлы в общие
координаты; внешняя ссылка, вставленная со сдвигом или поворотом, сдвигает
и поворачивает свой файл так же (xref_placements, там же).

Правило выбора: если в комплекте есть DWG, отдельные DXF не берутся -- это
почти всегда выгруженная копия того же чертежа, и её содержимое попало бы в
сцену дважды. Комплект только из DXF (тайлы подосновы в DXF) собирается
целиком. Пропущенные файлы попадают в предупреждения с причиной.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DRAWING_SUFFIXES = (".dwg", ".dxf")
ARCHIVE_SUFFIX = ".zip"

# Защита от архивов-бомб и мусора: число файлов в архиве и объём после
# распаковки. Чертежи объекта датасета -- десятки файлов и сотни мегабайт.
MAX_ARCHIVE_MEMBERS = 5000
MAX_UNPACKED_BYTES = 2 * 1024**3
# Архив в архиве -- бывает (папка объекта внутри поставки); глубже -- нет.
MAX_NESTING = 2

# Служебные файлы архиваторов и macOS -- не чертежи, даже с расширением .dwg.
_SERVICE_PARTS = ("paxheader", "__macosx")


class PackageError(ValueError):
    """Архив нельзя принять: битый, слишком большой. Текст -- для пользователя."""


@dataclass
class PackageSelection:
    drawings: list[Path] = field(default_factory=list)
    # {имя файла: причина} -- уйдут пользователю в dwgConversionWarnings.
    skipped: dict[str, str] = field(default_factory=dict)


def is_service_file(name: str) -> bool:
    parts = [p.lower() for p in PurePosixPath(name.replace("\\", "/")).parts]
    return any(p in _SERVICE_PARTS for p in parts) or (bool(parts) and parts[-1].startswith("._"))


def _member_name(info: zipfile.ZipInfo) -> str:
    """Имя файла в архиве. Без флага UTF-8 zipfile читает имя как cp437, а
    записано оно бывает в UTF-8 (zip на macOS и Linux) или в cp866 (русская
    Windows). Сначала пробуется UTF-8: байты cp866 корректным UTF-8 почти
    никогда не складываются, а наоборот -- складываются всегда."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "cp866"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def unique_path(dest_dir: Path, name: str) -> Path:
    """Путь в dest_dir под именем name; одноимённые файлы из разных папок
    архива получают суффикс «(2)», «(3)»."""
    target = dest_dir / name
    n = 2
    while target.exists():
        target = dest_dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


def extract_drawings(archive: Path, dest_dir: Path, depth: int = 0) -> list[Path]:
    """Чертежи (.dwg, .dxf) из zip -- в dest_dir, плоско, под своими именами;
    вложенные zip (не глубже MAX_NESTING) распаковываются тоже. Пути из архива в файловую систему не
    попадают (от «../» и абсолютных путей защищает плоская раскладка)."""
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise PackageError(f"{archive.name}: больше {MAX_ARCHIVE_MEMBERS} файлов в архиве")
            if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise PackageError(f"{archive.name}: после распаковки больше {MAX_UNPACKED_BYTES // 1024**3} ГБ")
            found: list[Path] = []
            for info in infos:
                name = _member_name(info)
                if is_service_file(name):
                    continue
                base = PurePosixPath(name.replace("\\", "/")).name
                suffix = Path(base).suffix.lower()
                if suffix not in DRAWING_SUFFIXES and not (suffix == ARCHIVE_SUFFIX and depth < MAX_NESTING):
                    continue
                target = unique_path(dest_dir, base)
                with zf.open(info) as src, target.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                if suffix == ARCHIVE_SUFFIX:
                    nested_dir = unique_path(dest_dir, f"{target.stem}.unzipped")
                    nested_dir.mkdir()
                    found += extract_drawings(target, nested_dir, depth + 1)
                    target.unlink()
                else:
                    found.append(target)
            return found
    except zipfile.BadZipFile as e:
        raise PackageError(f"{archive.name}: не zip-архив или архив повреждён") from e


def select_drawings(paths: list[Path]) -> PackageSelection:
    """Что пойдёт в сборку -- см. правило в докстринге модуля."""
    selection = PackageSelection()
    drawings = [p for p in paths if p.suffix.lower() in DRAWING_SUFFIXES and not is_service_file(p.name)]
    has_dwg = any(p.suffix.lower() == ".dwg" for p in drawings)
    for path in drawings:
        if has_dwg and path.suffix.lower() == ".dxf":
            selection.skipped[path.name] = "пропущен: в комплекте есть DWG, а DXF рядом с ними -- обычно выгруженная копия того же чертежа"
            continue
        selection.drawings.append(path)
    return selection
