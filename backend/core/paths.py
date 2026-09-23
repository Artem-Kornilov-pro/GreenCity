"""
Пути к файлам репозитория -- в одном месте. Модули бэкенда лежат в пакетах
(core/, greenplan/, ...), и расчёт вида Path(__file__).parent.parent давал бы
разный корень в зависимости от глубины файла; здесь он считается один раз.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

DATA_DIR = REPO_ROOT / "data"
NORMS_DIR = DATA_DIR / "norms"
LOCATIONS_DIR = REPO_ROOT / "locations"
PARSER_DIR = REPO_ROOT / "parser"
# Локально .env лежит в корне репозитория; в Docker переменные приходят из
# env_file, и load_dotenv без override их не перетирает.
ENV_FILE = REPO_ROOT / ".env"
