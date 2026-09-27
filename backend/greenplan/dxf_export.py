"""
Сцена с новой посадкой GreenPlan в DXF: исходные объекты, новая посадка на
отдельных слоях (по metadata.generated, см. export_dxf._object_layer) и
инженерные сети из scene.restrictions.
"""

from __future__ import annotations

import ezdxf

from core.plant_catalog import CatalogItem, catalog_by_id
from core.schemas import Scene
from exchange.export_dxf import scene_to_dxf
from greenplan.deterministic_placement import generate_for_scene
from greenplan.lawn import plan_lawns
from greenplan.pattern_assignment import ZoneAssignment


def export_greenplan_dxf(
    scene: Scene,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
    k: int = 3,
) -> tuple[ezdxf.document.Drawing, list[ZoneAssignment]]:
    """DXF-документ (исходная сцена и новая посадка) и решения по зонам --
    их можно передать в decision_report.generate_report."""
    new_objects, assignments = generate_for_scene(scene, trees, bushes, k)
    combined = scene.model_copy(update={"objects": [*scene.objects, *new_objects]})
    combined.lawns = plan_lawns(combined, catalog_by_id())
    return scene_to_dxf(combined), assignments
