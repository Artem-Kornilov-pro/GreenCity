"""
Кракозябры в именах слоёв: чертёж объявляет одну кодировку ($DWGCODEPAGE =
ANSI_1252 или ANSI_1251), а имена записаны в другой (UTF-8 или cp1251). Так бывает после
конвертации DWG -> DXF и у файлов, сохранённых нерусской версией CAD: слой
«Здания» читается как «Ð—Ð´Ð°Ð½Ð¸Ñ...», и правила его не узнают.

Чертёж с такими именами перечитывается в правильной кодировке
(mojibake_encoding); отдельное имя чинится repair_mojibake -- на случай, когда
перечитать нельзя (файл открыт в режиме восстановления).

Ещё одна форма того же: символы вне кодовой страницы чертежа CAD записывает
последовательностями \\U+XXXX («\\U+0422\\U+0435...»), и в имени слоя они так
и остаются; repair_mojibake раскодирует и их.
"""

from __future__ import annotations

import re
from typing import Optional

# Признак кракозябр: буквы Latin-1 (À-ÿ) или байты, которые cp1252 не
# декодирует (surrogateescape), и ни одной кириллической буквы.
_SUSPECT = re.compile("[À-ÿ\udc80-\udcff]")
_CYRILLIC = re.compile("[А-Яа-яЁё]")
_LETTER = re.compile(r"[^\W\d_]")
# Сколько имён смотреть, чтобы решить про весь чертёж.
_SAMPLE = 2000


def _raw_bytes(name: str) -> Optional[bytes]:
    raw = bytearray()
    for ch in name:
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF:
            raw.append(code - 0xDC00)
            continue
        try:
            raw += ch.encode("cp1252")
        except UnicodeEncodeError:
            if code > 0xFF:
                return None
            raw.append(code)
    return bytes(raw)


def _cyrillic_word(text: str) -> bool:
    letters = _LETTER.findall(text)
    return bool(letters) and len(_CYRILLIC.findall(text)) >= 0.6 * len(letters)


def _utf8_read_as_cp1251(name: str) -> Optional[str]:
    """«Р“Р°Р·РѕРЅ» -> «Газон»: имя в UTF-8, прочитанное как cp1251 (чертёж
    объявляет ANSI_1251, а записан в UTF-8). Обычное русское имя так не
    раскодируется: байты cp1251 почти никогда не образуют верный UTF-8."""
    if name.isascii():
        return None
    try:
        fixed = name.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    return fixed if fixed != name and _cyrillic_word(fixed) else None


def _decoded(name: str) -> Optional[tuple[str, str]]:
    """(исправленное имя, кодировка) или None, если имя -- не кракозябры."""
    if _CYRILLIC.search(name):
        fixed = _utf8_read_as_cp1251(name)
        return (fixed, "utf-8") if fixed else None
    if not _SUSPECT.search(name):
        return None
    raw = _raw_bytes(name)
    if raw is None:
        return None
    for encoding in ("utf-8", "cp1251"):
        try:
            fixed = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _cyrillic_word(fixed):
            return fixed, encoding
    return None


_UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def repair_mojibake(name: str) -> str:
    """«Ð—Ð´Ð°Ð½Ð¸Ñ...» и «\\U+0417\\U+0434...» -> «Здания»; обычное имя --
    без изменений."""
    if "\\U+" in name:
        name = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), name)
    found = _decoded(name)
    return found[0] if found else name


def mojibake_encoding(doc) -> Optional[str]:
    """Кодировка, в которой на самом деле записаны имена чертежа, если они
    читаются кракозябрами; None -- если с именами всё в порядке."""
    names = {layer.dxf.name for layer in doc.layers}
    for entity in doc.modelspace():
        if len(names) >= _SAMPLE:
            break
        names.add(entity.dxf.layer)
    votes: dict[str, int] = {}
    non_ascii = 0
    for name in names:
        if name.isascii():
            continue
        non_ascii += 1
        found = _decoded(name)
        if found:
            votes[found[1]] = votes.get(found[1], 0) + 1
    if not votes:
        return None
    encoding, count = max(votes.items(), key=lambda item: item[1])
    return encoding if count >= 0.3 * non_ascii else None
