"""
Финальный DXF -- GreenPlan, Этап 6 (issue #23: "Финальный DXF (слой
«сохранённое» + слой «новая посадка» + инженерные сети)"). Тонкая склейка уже
готовых кусков, а не новый экспорт с нуля:

* `deterministic_placement.generate_for_scene` (Этапы 3-5) уже считает новые
  объекты и решения по ним;
* `export_dxf.scene_to_dxf` уже умеет писать ЛЮБУЮ Scene в DXF по слоям
  (используется `/api/export-dxf`) -- инженерные сети сюда входят как есть,
  через `scene.restrictions`, отдельной обработки не требуют.

Разделение "сохранённое"/"новая посадка" на РАЗНЫЕ слои обеспечивает
export_dxf._object_layer() (метка metadata.generated, которую проставляет
deterministic_placement._scene_object) -- здесь достаточно просто передать
объединённую сцену дальше.
"""

from __future__ import annotations

import ezdxf
from deterministic_placement import generate_for_scene
from export_dxf import scene_to_dxf
from pattern_assignment import ZoneAssignment
from plant_catalog import CatalogItem
from schemas import Scene


def export_greenplan_dxf(
    scene: Scene,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
    k: int = 3,
) -> tuple[ezdxf.document.Drawing, list[ZoneAssignment]]:
    """(DXF-документ с исходной сценой + новой посадкой, решения по зонам).

    `assignments` возвращается вызывающему коду, а не используется здесь же,
    чтобы он мог передать их в decision_report.generate_report(assignments)
    для второго артефакта Этапа 6 (текст-объяснение) -- без прямой связи
    между этим модулем и decision_report.py."""
    new_objects, assignments = generate_for_scene(scene, trees, bushes, k)
    combined = scene.model_copy(update={"objects": [*scene.objects, *new_objects]})
    return scene_to_dxf(combined), assignments
