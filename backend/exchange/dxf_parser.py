"""
Мост к parser/parse_dxf.py. Парсер живёт отдельной папкой parser/ (его же
запускают из командной строки: python3 parser/parse_dxf.py file.dxf), вне
пакетов backend, поэтому путь к нему добавляется в sys.path -- здесь, в одном
месте, а не в каждом модуле, которому нужен разбор DXF.
"""

import sys

from core.paths import PARSER_DIR

if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from parse_dxf import parse_dxf_doc, parse_dxf_file  # noqa: E402

__all__ = ["parse_dxf_doc", "parse_dxf_file"]
