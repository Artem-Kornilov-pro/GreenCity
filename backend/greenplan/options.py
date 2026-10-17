"""Параметры GreenPlan, которые пользователь задаёт перед запуском."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

STYLE_CHOICE_LABELS: dict[str, str] = {
    "auto": "по похожим проектам",
    "regular": "регулярный",
    "landscape": "пейзажный",
}


class GreenPlanOptions(BaseModel):
    """Параметры озеленения. По умолчанию: стиль по аналогам; деревья,
    кустарники и газон; без благоустройства."""

    style: Literal["auto", "regular", "landscape"] = Field(
        default="auto", description="auto -- по похожим проектам, regular -- регулярный, landscape -- пейзажный."
    )
    trees: bool = Field(default=True, description="Сажать деревья.")
    bushes: bool = Field(default=True, description="Сажать кустарники.")
    lawn: bool = Field(default=True, description="Устраивать газон на свободной земле.")
    root_barrier: bool = Field(
        default=False,
        description=(
            "Сажать деревья ближе нормы к подземным сетям с прикорневым барьером "
            "(СП 42.13330.2016, прим. 4-7 к табл. 9.1): такие деревья помечаются «условно, с корнезащитой»."
        ),
    )
    remove_violating_plants: bool = Field(
        default=False,
        description="Убрать существующие деревья и кусты с нарушением норм отступов до расстановки; их место засаживается заново.",
    )
    preferred_trees: list[str] = Field(
        default=[], description="Предпочтительные виды деревьев (id каталога species_*). Ставятся первыми, если проходят нормы."
    )
    preferred_bushes: list[str] = Field(default=[], description="Предпочтительные виды кустарников (id каталога species_*).")
    paths: bool = Field(default=False, description="Проложить новые дорожки во дворах: от подъездов к подъездам и к парковкам.")
    lighting: bool = Field(default=False, description="Фонари вдоль новых и существующих дорожек.")
    benches: bool = Field(default=False, description="Скамейки с урнами вдоль новых дорожек.")

    def summary(self, labels: dict[str, str]) -> list[str]:
        """Строки «параметр: значение» для пояснительной записки; labels --
        id каталога -> название вида."""
        planting = [name for name, on in (("деревья", self.trees), ("кустарники", self.bushes), ("газон", self.lawn)) if on]
        improvements = [
            name for name, on in (("дорожки", self.paths), ("освещение", self.lighting), ("скамейки и урны", self.benches)) if on
        ]
        rows = [
            f"стиль участка: {STYLE_CHOICE_LABELS[self.style]}",
            f"посадки: {', '.join(planting) if planting else 'не заданы'}",
        ]
        if self.root_barrier:
            rows.append("деревья у подземных сетей: допускаются с прикорневым барьером (условно)")
        if self.remove_violating_plants:
            rows.append("существующие деревья и кусты с нарушением норм: удалены")
        for title, ids in (("предпочтительные деревья", self.preferred_trees), ("предпочтительные кустарники", self.preferred_bushes)):
            if ids:
                rows.append(f"{title}: {', '.join(labels.get(i, i) for i in ids)}")
        rows.append(f"благоустройство: {', '.join(improvements) if improvements else 'не добавлялось'}")
        return rows
