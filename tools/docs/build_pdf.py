"""
PDF-версия документации для сдачи: документация решения, алгоритм GreenPlan,
запуск и технический обзор в одном файле docs/pdf/GreenCity_documentation.pdf.

Нужны пакет markdown (pip install markdown) и Chromium или Google Chrome:
путь берётся из переменной CHROME, иначе ищется среди установленных.

    CHROME=/path/to/chrome python tools/docs/build_pdf.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
OUT = DOCS / "pdf" / "GreenCity_documentation.pdf"
REPO_URL = "https://github.com/Artem-Kornilov-pro/GreenCity/blob/main"

# (файл, заголовок в оглавлении)
PARTS = [
    (DOCS / "SOLUTION.md", "Документация решения"),
    (DOCS / "GREENPLAN_ALGORITHM.md", "Алгоритм GreenPlan"),
    (ROOT / "README.md", "Запуск и разработка"),
    (DOCS / "TECHNICAL_OVERVIEW.md", "Технический обзор"),
]

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: "PT Sans", "Helvetica Neue", Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1d1d1d; }
h1 { font-size: 20pt; margin: 0 0 10pt; page-break-before: always; }
.cover h1 { page-break-before: avoid; }
h2 { font-size: 14pt; margin: 16pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; }
h2, h3 { page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 3pt 5pt; vertical-align: top; text-align: left; overflow-wrap: break-word; }
td code, td a { overflow-wrap: anywhere; }
th { background: #eef3ee; }
code { font-family: "JetBrains Mono", Menlo, Consolas, monospace; font-size: 8.8pt; background: #f3f3f3; padding: 0 2pt; overflow-wrap: anywhere; }
pre { background: #f5f5f5; padding: 6pt 8pt; font-size: 8.5pt; white-space: pre-wrap; overflow-wrap: anywhere; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; max-height: 250mm; display: block; margin: 6pt auto; }
p:has(> img) { page-break-before: always; page-break-after: always; text-align: center; }
a { color: #1f5f2f; text-decoration: none; }
.cover { height: 240mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
.cover h1 { font-size: 30pt; }
.cover p { font-size: 13pt; margin: 4pt 0; }
.toc li { margin: 3pt 0; font-size: 12pt; }
"""


def _slug(text: str, separator: str) -> str:
    """Якорь как у GitHub: кириллица сохраняется, пунктуация убирается."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", separator)


def _nest_lists(text: str) -> str:
    """python-markdown вкладывает списки по 4 пробелам, а документы пишутся с 2."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("  ") and line.strip():
            indent = len(line) - len(line.lstrip(" "))
            line = " " * (indent * 2) + line.lstrip(" ")
        out.append(line)
    return "\n".join(out)


def _fix_links(html: str, source: Path, part_ids: dict[Path, str]) -> str:
    def repl(match: re.Match) -> str:
        href = match.group(1)
        if re.match(r"^[a-z]+:", href) or href.startswith("#"):
            return match.group(0)
        path, _, anchor = href.partition("#")
        target = (source.parent / path).resolve()
        if target in part_ids:
            return f'href="#{anchor or part_ids[target]}"'
        if target.suffix in (".svg", ".png"):
            return match.group(0)
        try:
            rel = target.relative_to(ROOT).as_posix()
        except ValueError:
            return match.group(0)
        return f'href="{REPO_URL}/{rel}{"#" + anchor if anchor else ""}"'

    html = re.sub(r'href="([^"]+)"', repl, html)
    # Картинки -- абсолютным путём: HTML собирается во временном каталоге.
    return re.sub(r'src="([^":]+)"', lambda m: f'src="{(source.parent / m.group(1)).resolve().as_uri()}"', html)


def build_html() -> str:
    part_ids = {path.resolve(): f"part-{i}" for i, (path, _) in enumerate(PARTS)}
    body = [
        '<section class="cover"><h1>GreenCity</h1>',
        "<p>Автоматическое проектирование озеленения с учётом подземных коммуникаций и городской среды</p>",
        "<p>Хакатон «Лидеры цифровой трансформации 2026», команда «Вороны»</p>",
        "<p>Артемий Корнилов, Константин Жадько</p>",
        '<h2>Содержание</h2><ol class="toc">',
        *(f'<li><a href="#part-{i}">{escape(title)}</a></li>' for i, (_, title) in enumerate(PARTS)),
        "</ol></section>",
    ]
    for i, (path, _) in enumerate(PARTS):
        text = _nest_lists(path.read_text(encoding="utf-8"))
        md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists", "toc"],
            extension_configs={"toc": {"slugify": _slug}},
        )
        html = md.convert(text)
        html = re.sub(r'<h1 id="[^"]*"', f'<h1 id="part-{i}"', html, count=1)
        body.append(f"<article>{_fix_links(html, path, part_ids)}</article>")
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>GreenCity — документация</title>'
        f"<style>{CSS}</style></head><body>{''.join(body)}</body></html>"
    )


def find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        *sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")),
        *sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    sys.exit("Chromium не найден: укажите путь в переменной CHROME")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "doc.html"
        page.write_text(build_html(), encoding="utf-8")
        subprocess.run(
            [
                find_chrome(),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--allow-file-access-from-files",
                f"--print-to-pdf={OUT}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"готово: {OUT}")


if __name__ == "__main__":
    main()
