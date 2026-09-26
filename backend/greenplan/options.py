"""
Параметры GreenPlan, которые задаёт пользователь перед запуском (диалог на
панели редактора). По умолчанию -- прежнее поведение: стиль по аналогам,
деревья, кустарники и газон, без благоустройства.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

STYLE_CHOICE_LABELS: dict[str, str] = {
    "auto": "по похожим проектам",
    "regular": "регулярный",
    "landscape": "пейзажный",
}


class GreenPlanOptions(BaseModel):
    # Стиль участка: "auto" -- по голосованию похожих проектов
    # (pattern_assignment._site_style), иначе задан пользователем.
    style: Literal["auto", "regular", "landscape"] = "auto"

    # Что сажать.
    trees: bool = True
    bushes: bool = True
    lawn: bool = True

    # Предпочтительные виды -- id каталога (species_*). Ставятся первыми в
    # любой роли своей категории, но только если проходят нормы (ассортимент
    # для типа территории, 369-ПП, коды ограничений, колючие у дорожек,
    # широкая крона у здания); почему вид не использован -- в notes ответа.
    preferred_trees: list[str] = []
    preferred_bushes: list[str] = []

    # Благоустройство (greenplan/improvements.py): новые дорожки во дворах,
    # фонари вдоль новых и существующих дорожек, скамейки с урнами вдоль новых.
    paths: bool = False
    lighting: bool = False
    benches: bool = False

    def summary(self, labels: dict[str, str]) -> list[str]:
        """Строки "параметр: значение" для пояснительной записки; labels --
        id каталога -> название вида."""
        planting = [name for name, on in (("деревья", self.trees), ("кустарники", self.bushes), ("газон", self.lawn)) if on]
        improvements = [
            name for name, on in (("дорожки", self.paths), ("освещение", self.lighting), ("скамейки и урны", self.benches)) if on
        ]
        rows = [
            f"стиль участка: {STYLE_CHOICE_LABELS[self.style]}",
            f"посадки: {', '.join(planting) if planting else 'не заданы'}",
        ]
        for title, ids in (("предпочтительные деревья", self.preferred_trees), ("предпочтительные кустарники", self.preferred_bushes)):
            if ids:
                rows.append(f"{title}: {', '.join(labels.get(i, i) for i in ids)}")
        rows.append(f"благоустройство: {', '.join(improvements) if improvements else 'не добавлялось'}")
        return rows
