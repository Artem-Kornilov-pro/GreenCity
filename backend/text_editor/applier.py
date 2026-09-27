"""
Применение плана правки текстом: PlanApplier выполняет операции от LLM
(text_editor/operations.py) детерминированно поверх placement.Placer. Невыполнимая
операция -- не исключение, а запись в rejected с человекочитаемой причиной:
одна сломанная операция от модели не роняет весь план.

Операции разнесены по файлам: здесь -- общие шаги и точечные add/remove/
move/rotate, в text_editor/ops_placement.py -- групповые посадки, в
text_editor/ops_editing.py -- правка существующего, дорожки, зоны и дизайн двора.
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Optional

from pydantic import ValidationError

from core.placement import (
    MAX_SNAP_DISTANCE_M,
    Placer,
)
from core.plant_catalog import CatalogItem
from core.schemas import Point3, RestrictionZone, Scene, SceneObject
from core.setback_norms import SpeciesArg
from greenplan.options import GreenPlanOptions
from text_editor.operations import (
    _OPERATION,
    AddOp,
    AlignAlongOp,
    ConnectOp,
    CoverAreaOp,
    DefineZoneOp,
    DesignAreaOp,
    DuplicateNearOp,
    EncloseOp,
    FaceOp,
    LineOfOp,
    LlmPlan,
    MoveOp,
    PlaceAlongOp,
    PlaceInAreaOp,
    RemoveOp,
    RemoveWhereOp,
    ReplaceWhereOp,
    ResizeOp,
    RotateOp,
    RunGreenPlanOp,
    SetCountOp,
    TextEditResult,
    ThinOutOp,
)
from text_editor.ops_editing import EditingOpsMixin
from text_editor.ops_placement import PlacementOpsMixin
from text_editor.plan_common import GOLDEN_ANGLE_DEG, _is_oriented, _labels, _normalize
from text_editor.prompt import _editable_types

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")


class PlanApplier(PlacementOpsMixin, EditingOpsMixin):
    def __init__(self, scene: Scene, catalog: list[CatalogItem], placer: Placer):
        self.scene = scene
        self.catalog = catalog
        self.by_id = {item.id: item for item in catalog}
        self.editable = _editable_types(catalog)
        self.category_types: dict[str, set[str]] = {}
        for item in catalog:
            self.category_types.setdefault(item.category, set()).add(item.object_type)
        self.placer = placer
        self.objects = {o.id: o for o in scene.objects}
        self.applied: list[str] = []
        self.rejected: list[str] = []
        self.warnings: list[str] = []
        self.new_zones: list[RestrictionZone] = []  # define_zone -- добавляются в вывод сцены отдельно
        self.greenplan: Optional[GreenPlanOptions] = None  # run_greenplan -- запускает фронтенд
        self.created: list[str] = []  # id новых объектов -- в историю чата ("убери их")

    def run(self, plan: LlmPlan) -> TextEditResult:
        handlers = {
            AddOp: self.add,
            RemoveOp: self.remove,
            MoveOp: self.move,
            RotateOp: self.rotate,
            PlaceAlongOp: self.place_along,
            PlaceInAreaOp: self.place_in_area,
            RemoveWhereOp: self.remove_where,
            ConnectOp: self.connect,
            CoverAreaOp: self.cover_area,
            ReplaceWhereOp: self.replace_where,
            ThinOutOp: self.thin_out,
            ResizeOp: self.resize,
            FaceOp: self.face,
            AlignAlongOp: self.align_along,
            LineOfOp: self.line_of,
            EncloseOp: self.enclose,
            DuplicateNearOp: self.duplicate_near,
            SetCountOp: self.set_count,
            DefineZoneOp: self.define_zone,
            DesignAreaOp: self.design_area,
            RunGreenPlanOp: self.run_greenplan,
        }
        for number, raw in enumerate(plan.operations, 1):
            normalized = _normalize(raw)
            try:
                op = _OPERATION.validate_python(normalized)
            except ValidationError as e:
                error = e.errors()[0]
                # loc вида ("place_along", "target"): первый элемент -- тег операции.
                field = ".".join(str(part) for part in error["loc"][1:])
                detail = f"{field}: {error['msg']}" if field else error["msg"]
                self.rejected.append(f"операция {number} ({raw.get('op', '?')}): не разобрать — {detail}")
                continue
            # Лишние поля pydantic молча отбрасывает -- а за ними стоит
            # намерение модели ("у площадки", "только клёны"), которое тогда
            # тихо терялось. Пусть это будет видно.
            unknown = sorted(set(normalized) - set(type(op).model_fields))
            if unknown:
                self.warnings.append(f"операция {number} ({op.op}): не поддерживается и пропущено — {', '.join(unknown)}")
            handlers[type(op)](op)

        # Исходную сцену не трогаем: при ошибке посередине у фронтенда
        # остаётся прежняя.
        scene = self.scene.model_copy(
            update={
                "objects": list(self.objects.values()),
                "restrictions": [*self.scene.restrictions, *self.new_zones],
            }
        )
        return TextEditResult(
            scene=scene,
            explanation=self._explanation(plan),
            applied=self.applied,
            rejected=self.rejected,
            warnings=self.warnings,
            greenplan=self.greenplan,
            added_ids=[i for i in self.created if i in self.objects],
        )

    def _explanation(self, plan: LlmPlan) -> str:
        """Ответ модели пишется до применения плана и всегда звучит как успех
        ("площадка огорожена") -- даже когда планировщик всё отклонил. Если
        не сделано ничего, пользователь должен это увидеть сразу, а не
        разбирать мелкий список отказов под бодрым ответом."""
        if self.applied or not self.rejected:
            return plan.explanation
        reasons = "; ".join(self.rejected[:2])
        return f"Не получилось: {reasons}."

    # --- Общие шаги --------------------------------------------------------

    def _pool(self, catalog_ids: list[str], what: str) -> Optional[list[CatalogItem]]:
        items = []
        unknown = []
        for catalog_id in dict.fromkeys(catalog_ids):
            item = self.by_id.get(catalog_id)
            if item is None:
                unknown.append(catalog_id)
            else:
                items.append(item)
        if unknown:
            target = self.warnings if items else self.rejected
            target.append(f"{what}: нет в каталоге: {', '.join(unknown)}")
        return items or None

    def _create(self, item: CatalogItem, x: float, z: float, rotation_deg: float) -> None:
        new_id = f"{item.object_type}_llm_{uuid.uuid4().hex[:8]}"
        self.objects[new_id] = SceneObject(
            id=new_id,
            type=item.object_type,
            model=item.model,
            position=Point3(x=x, y=0.0, z=z),
            rotation=math.radians(rotation_deg),
            scale=1.0,
            metadata={"catalogId": item.id, "label": item.label, "species": item.label, "source": "llm"},
        )
        self.placer.occupy(new_id, x, z, item.object_type)
        self.created.append(new_id)

    def _plant(self, items: list[CatalogItem], spots: list[tuple]) -> str:
        """Создать объекты в точках, чередуя виды; вернуть подписи видов."""
        for i, spot in enumerate(spots):
            item = items[i % len(items)]
            rotation = spot[2] if len(spot) > 2 and _is_oriented(item) else (i * GOLDEN_ANGLE_DEG) % 360
            self._create(item, spot[0], spot[1], rotation)
        return _labels(items[: len(spots)])

    def _spot(self, x: float, z: float, kind: Optional[str], action: str, what: str, species: SpeciesArg = None):
        """Точка для точечной операции: сама (x, z), если там можно, иначе
        ближайшая допустимая. None -- операция отклонена (причина записана)."""
        reason = None if self.placer.is_free(x, z, kind, species=species) else self.placer.explain(x, z, kind, species=species)
        spot = self.placer.nearest_free(x, z, kind, species=species)
        if spot is None:
            self.rejected.append(
                f"{action} в ({x:.1f}, {z:.1f}): {reason}; в радиусе {MAX_SNAP_DISTANCE_M:.0f} м нет места без нарушений"
            )
            return None
        shift = math.hypot(spot[0] - x, spot[1] - z)
        if reason is not None and shift > 0.05:
            self.warnings.append(f"{what}: сдвинуто на {shift:.1f} м, чтобы соблюсти норму: {reason}")
        return spot

    def _editable(self, action: str, obj_id: str) -> Optional[SceneObject]:
        obj = self.objects.get(obj_id)
        if obj is None:
            self.rejected.append(f"{action} {obj_id}: такого объекта нет")
            return None
        if obj.type not in self.editable:
            self.rejected.append(f"{action} {obj_id}: объект «{obj.type}» менять нельзя")
            return None
        return obj

    def _setback_kind(self, obj: SceneObject) -> Optional[str]:
        catalog_id = obj.metadata.get("catalogId")
        if catalog_id in self.by_id:
            return self.by_id[catalog_id].setback_kind
        for item in self.catalog:
            if item.object_type == obj.type:
                return item.setback_kind
        return None

    # --- Точечные операции -------------------------------------------------


    def _species(self, obj: SceneObject) -> Optional[str]:
        """Вид существующего объекта -- для правил по породе
        (setback_norms.py). metadata.species ставят парсер/GreenPlan/_create;
        у объектов без него -- подпись каталожной записи."""
        species = obj.metadata.get("species") or obj.metadata.get("label")
        if species:
            return str(species)
        item = self.by_id.get(obj.metadata.get("catalogId"))
        return item.label if item else None
    def add(self, op: AddOp) -> None:
        item = self.by_id.get(op.catalog_id)
        if item is None:
            self.rejected.append(f"add {op.catalog_id}: такого вида нет в каталоге")
            return
        spot = self._spot(op.x, op.z, item.setback_kind, f"add «{item.label}»", f"«{item.label}»", species=item.label)
        if spot is None:
            return
        self._create(item, spot[0], spot[1], op.rotation_deg)
        self.applied.append(f"добавлено «{item.label}» в ({spot[0]:.1f}, {spot[1]:.1f})")

    def remove(self, op: RemoveOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        del self.objects[obj.id]
        self.placer.release(obj.id)
        self.applied.append(f"удалён {obj.id}")

    def move(self, op: MoveOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        # Сам объект не должен мешать себе на новом месте.
        self.placer.release(obj.id)
        spot = self._spot(op.x, op.z, self._setback_kind(obj), f"move {obj.id}", obj.id, species=self._species(obj))
        if spot is None:
            self.placer.occupy(obj.id, obj.position.x, obj.position.z, obj.type)
            return
        x, z = spot
        self.objects[obj.id] = obj.model_copy(update={"position": Point3(x=x, y=obj.position.y, z=z)})
        self.placer.occupy(obj.id, x, z, obj.type)
        self.applied.append(f"перемещён {obj.id} в ({x:.1f}, {z:.1f})")

    def rotate(self, op: RotateOp) -> None:
        obj = self._editable(op.op, op.id)
        if obj is None:
            return
        self.objects[obj.id] = obj.model_copy(update={"rotation": math.radians(op.rotation_deg)})
        self.applied.append(f"повёрнут {obj.id} на {op.rotation_deg:.0f}°")

    # --- GreenPlan -----------------------------------------------------------

    def _preferred(self, ids: list[str], category: str, what: str) -> list[str]:
        """Предпочтительные виды GreenPlan: только конкретные виды каталога
        (species_*) своей категории -- как в диалоге параметров на фронтенде."""
        chosen = []
        for catalog_id in dict.fromkeys(ids):
            item = self.by_id.get(catalog_id)
            if item is None or not item.id.startswith("species_") or item.category != category or item.object_type != category:
                self.warnings.append(f"GreenPlan: «{catalog_id}» не {what} из каталога видов — пропущено")
            else:
                chosen.append(item.id)
        return chosen

    def run_greenplan(self, op: RunGreenPlanOp) -> None:
        if self.greenplan is not None:
            self.warnings.append("GreenPlan запрошен дважды — взяты параметры последнего запроса")
        values = op.model_dump(exclude={"op", "preferred_trees", "preferred_bushes"}, exclude_none=True)
        self.greenplan = GreenPlanOptions(
            **values,
            preferred_trees=self._preferred(op.preferred_trees, "tree", "дерево"),
            preferred_bushes=self._preferred(op.preferred_bushes, "bush", "кустарник"),
        )
        labels = {item.id: item.label for item in self.catalog}
        self.applied.append("GreenPlan озеленит участок: " + "; ".join(self.greenplan.summary(labels)))


def apply_plan(
    scene: Scene,
    plan: LlmPlan,
    catalog: list[CatalogItem],
    placer: Optional[Placer] = None,
) -> TextEditResult:
    return PlanApplier(scene, catalog, placer or Placer(scene)).run(plan)
