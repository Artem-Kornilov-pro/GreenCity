"""
Инвазивные виды по ППМ 369-ПП, приложение 1 (data/norms/369-pp/invasive.yaml)
-- видовой запрет, в отличие от всех остальных норм проекта, которые
геометрические (отступы). Сажать нельзя ни один вид перечня: группа III прямо
запрещена к высадке, группы I и II подлежат удалению.

Применяется в одном месте -- plant_catalog.load_catalog() исключает такие
виды из каталога посадок, поэтому их не предложат ни GreenPlan, ни правка
текстом, ни панель "Добавить объект" на фронте.

Сравнение -- по русскому названию, а не по роду (как правила отступа в
setback_norms.py): запрещён дуб КРАСНЫЙ, а не дуб вообще. Название
нормализуется (регистр, ё/е, кавычки, пояснение в скобках) и считается
запрещённым, если совпадает с названием из перечня или начинается с него
("Роза морщинистая (формы и сорта)", "Дерен белый 'Элегантиссима'"). Такое
правило осторожное: "Девичий виноград пятилисточковый" тоже попадает под
запрет "Девичий виноград" -- см. needs_check в invasive.yaml.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

_INVASIVE_YAML = Path(__file__).resolve().parent.parent / "data" / "norms" / "369-pp" / "invasive.yaml"


class InvasiveSpecies(BaseModel):
    number: str
    name_ru: str
    name_la: str
    group: str
    measure: str


def normalize_species_name(name: str) -> str:
    """"Роза морщинистая (формы и сорта)" -> "роза морщинистая"."""
    text = re.sub(r"\(.*?\)", " ", name.lower().replace("ё", "е"))
    text = re.sub(r"[\"'«»“”]", " ", text)
    return " ".join(text.split())


@lru_cache(maxsize=1)
def _index() -> dict[str, InvasiveSpecies]:
    """Нормализованное название (и каждый синоним) -> запись перечня."""
    raw = yaml.safe_load(_INVASIVE_YAML.read_text(encoding="utf-8"))
    index: dict[str, InvasiveSpecies] = {}
    for group, body in raw["groups"].items():
        for entry in body["species"]:
            species = InvasiveSpecies(
                number=entry["item"], name_ru=entry["ru"], name_la=entry["la"], group=group, measure=body["measure"]
            )
            for name in (entry["ru"], *entry.get("aliases", ())):
                index[normalize_species_name(name)] = species
    return index


def all_invasive_species() -> list[InvasiveSpecies]:
    return list({species.name_ru: species for species in _index().values()}.values())


def invasive_match(name: Optional[str]) -> Optional[InvasiveSpecies]:
    """Запись перечня 369-ПП, под которую попадает этот вид, или None."""
    if not name:
        return None
    normalized = normalize_species_name(name)
    for banned, species in _index().items():
        if normalized == banned or normalized.startswith(banned + " "):
            return species
    return None
