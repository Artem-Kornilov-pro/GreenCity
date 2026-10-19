"""
Версии, по которым построен результат: приложение, правила разбора чертежа,
нормы, корпус прецедентов GreenPlan. Вместе с SHA-256 исходного чертежа они
идут в объяснения, записку, проверку выгрузки и заголовок выходного DXF --
по ним видно, каким файлом и какими правилами получен план, и тот же файл с
теми же версиями даёт тот же план.

Версия правил и норм -- хеш содержимого их файлов: меняется любая строка
синонимов слоёв или таблицы отступов -- меняется и версия.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from core.paths import REPO_ROOT

APP_VERSION = "1.1.2"

_PARSER_RULES = ("parser/dxf_parsing/*.py", "parser/parse_dxf.py")
_NORMS = ("backend/core/setback_norms.py", "data/norms/**/*")
_CORPUS = ("data/pattern_corpus.yaml", "data/pattern_corpus_features.json")


def _digest(patterns: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for pattern in patterns:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8") + b"\0")
                # Без перевода строк: checkout в Windows не меняет версию.
                digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


@lru_cache(maxsize=1)
def versions() -> dict[str, str]:
    """{что: версия} -- приложение и хеши правил, норм и корпуса."""
    return {
        "app": APP_VERSION,
        "parser_rules": _digest(_PARSER_RULES),
        "norms": _digest(_NORMS),
        "precedent_corpus": _digest(_CORPUS),
    }


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_files(paths: list[Path]) -> str:
    """Один хеш комплекта файлов: имена и содержимое в порядке загрузки."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
