"""Пути к каталогам репозитория."""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

DATA_DIR = REPO_ROOT / "data"
NORMS_DIR = DATA_DIR / "norms"
LOCATIONS_DIR = REPO_ROOT / "locations"
PARSER_DIR = REPO_ROOT / "parser"
ENV_FILE = REPO_ROOT / ".env"
