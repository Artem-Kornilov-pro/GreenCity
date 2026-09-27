"""
Словарь операций правки плана текстом (text_editor/service.py): pydantic-модели того,
что может вернуть LLM, -- от точечных add/remove/move/rotate до групповых
place_along/cover_area/design_area. Модель выбирает НАМЕРЕНИЕ и параметры,
координаты считает планировщик (text_editor/applier.py). Здесь же -- запрос и
ответ /api/edit-with-text (TextEditRequest/TextEditResult).
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from core.schemas import Scene
from greenplan.options import GreenPlanOptions

# --- Операции, которые может вернуть модель ---------------------------------

# Цель -- встроенный тип или имя зоны из define_zone; проверяется при
# применении (Placer.target_geometry), имена зон заранее не известны.
Target = str


class AddOp(BaseModel):
    op: Literal["add"]
    catalog_id: str
    x: float
    z: float
    rotation_deg: float = 0.0


class RemoveOp(BaseModel):
    op: Literal["remove"]
    id: str


class MoveOp(BaseModel):
    op: Literal["move"]
    id: str
    x: float
    z: float


class RotateOp(BaseModel):
    op: Literal["rotate"]
    id: str
    rotation_deg: float


class PlaceAlongOp(BaseModel):
    """Ряд посадок вдоль контура цели, с обеих сторон."""

    op: Literal["place_along"]
    target: Target
    catalog_ids: list[str]
    spacing_m: Optional[float] = None
    offset_m: Optional[float] = None
    max_count: Optional[int] = None
    # Ограничить ряд кругом (например, "вдоль дорожки у третьего подъезда").
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class PlaceInAreaOp(BaseModel):
    """Группа посадок: компактно вокруг точки или равномерно по области."""

    op: Literal["place_in_area"]
    catalog_ids: list[str]
    # По умолчанию, а не обязательно: в просьбе «посади разные виды» числа нет,
    # и модель иногда пропускает count.
    count: int = 5
    spacing_m: Optional[float] = None
    area: Optional[str] = None  # id из free_areas контекста
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # "У детской площадки", "вдоль парковки группой": у цели, не дальше
    # distance_m от её края (по умолчанию DEFAULT_NEAR_TARGET_M).
    target: Optional[Target] = None
    distance_m: Optional[float] = None


class RemoveWhereOp(BaseModel):
    """Удалить все объекты заданных типов, подходящие под фильтры. Пустой
    object_types -- все редактируемые типы: "очисти эту зону" не должно
    требовать перечислять всё, что там может стоять."""

    op: Literal["remove_where"]
    object_types: list[str] = []
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class ConnectOp(BaseModel):
    """Проложить дорожку между двумя точками: подъезд-подъезд, подъезд-объект,
    сеть-зона. Каждый конец -- ровно одно из: id объекта, цель (target) или
    координаты. Для точечной связи, которую не строит design_area."""

    op: Literal["connect"]
    from_id: Optional[str] = None
    from_target: Optional[Target] = None
    from_x: Optional[float] = None
    from_z: Optional[float] = None
    to_id: Optional[str] = None
    to_target: Optional[Target] = None
    to_x: Optional[float] = None
    to_z: Optional[float] = None


class CoverAreaOp(BaseModel):
    """Сплошной ковёр травяного покрытия/цветника по области -- в отличие
    от place_in_area (редкая равномерная россыпь), плитки укладываются
    почти встык. ТЗ прямо называет травянистые покрытия отдельным видом
    посадки наравне с деревьями и кустами -- редкая россыпь газонных плиток
    для этого не годится."""

    op: Literal["cover_area"]
    catalog_ids: list[str]
    area: Optional[str] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class ReplaceWhereOp(BaseModel):
    """Заменить вид у существующих объектов на другой, не трогая их
    расположение и поворот: "замени низкие деревья на высокие", "сделай
    кусты вдоль дорожек разнообразнее"."""

    op: Literal["replace_where"]
    object_types: list[str] = []  # пусто -- все редактируемые типы
    catalog_ids: list[str]
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class ThinOutOp(BaseModel):
    """Убрать лишние объекты заданных типов там, где они стоят гуще
    заданного шага -- проредить слишком плотную посадку (например, из
    исходных данных), не убирая всё целиком."""

    op: Literal["thin_out"]
    object_types: list[str] = []  # пусто -- все редактируемые типы
    min_spacing_m: Optional[float] = None
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class ResizeOp(BaseModel):
    """Изменить масштаб существующих объектов (взрослые/молодые деревья,
    визуальный акцент), не трогая расположение."""

    op: Literal["resize"]
    object_types: list[str] = []  # пусто -- все редактируемые типы
    scale: float
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class FaceOp(BaseModel):
    """Развернуть существующие объекты (лавки, фонари) к точке, объекту или
    цели -- "разверни лавки к фонтану", "разверни фонари к дорожке"."""

    op: Literal["face"]
    object_types: list[str] = []  # пусто -- все редактируемые типы
    at_id: Optional[str] = None
    at_target: Optional[Target] = None
    at_x: Optional[float] = None
    at_z: Optional[float] = None
    # Фильтр "какие из объектов трогать" (как в remove_where) -- не путать с
    # at_*, которые задают, КУДА они должны смотреть.
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class AlignAlongOp(BaseModel):
    """Подровнять уже стоящие объекты в аккуратный ряд вдоль цели (дорожки,
    фасада, границы участка) -- передвигает существующие объекты на
    валидные места ряда, а не добавляет новые. Для вразнобой расставленных
    вручную или унаследованных из DXF объектов."""

    op: Literal["align_along"]
    object_types: list[str]
    target: Target
    spacing_m: Optional[float] = None
    offset_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class LineOfOp(BaseModel):
    """Ряд объектов (изгородь, забор из фонарей и т.п.) по прямой между
    двумя точками -- в отличие от place_along (вдоль контура существующей
    цели), концы линии произвольные: объект, цель или координаты."""

    op: Literal["line_of"]
    catalog_ids: list[str]
    from_id: Optional[str] = None
    from_target: Optional[Target] = None
    from_x: Optional[float] = None
    from_z: Optional[float] = None
    to_id: Optional[str] = None
    to_target: Optional[Target] = None
    to_x: Optional[float] = None
    to_z: Optional[float] = None
    spacing_m: Optional[float] = None


class EncloseOp(BaseModel):
    """Кольцо объектов (изгородь, забор, фонари) вокруг существующего
    объекта, цели или точки -- "огороди детскую площадку живой изгородью",
    "обведи фонтан клумбами"."""

    op: Literal["enclose"]
    catalog_ids: list[str]
    around_id: Optional[str] = None
    around_target: Optional[Target] = None
    around_x: Optional[float] = None
    around_z: Optional[float] = None
    radius_m: Optional[float] = None  # для around_id / around_x,z — радиус кольца
    offset_m: Optional[float] = None
    spacing_m: Optional[float] = None


class DuplicateNearOp(BaseModel):
    """Скопировать существующий объект (тот же вид, тот же поворот) рядом с
    другой точкой/объектом/целью -- "сделай такую же лавку у второго
    подъезда" без пересоздания параметров вручную."""

    op: Literal["duplicate_near"]
    id: str
    near_id: Optional[str] = None
    near_target: Optional[Target] = None
    near_x: Optional[float] = None
    near_z: Optional[float] = None
    count: int = 1


class SetCountOp(BaseModel):
    """Довести суммарное число объектов заданных типов (по всему участку,
    у цели или в области) ровно до count -- добавляет недостающие из
    catalog_ids или убирает лишние, смотря что нужно."""

    op: Literal["set_count"]
    object_types: list[str] = []  # пусто -- все редактируемые типы
    count: int
    catalog_ids: list[str] = []
    target: Optional[Target] = None
    distance_m: Optional[float] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    # Фильтр по виду (без учёта падежа и ё) и/или по id, вместе с остальными -- через И.
    species: list[str] = []
    ids: list[str] = []


class DefineZoneOp(BaseModel):
    """Выделить именованную зону под назначение («детская зона», «здесь
    ничего не сажать»). Зона попадает в зоны ограничений и доступна по имени
    в target и area в этой и следующих правках."""

    op: Literal["define_zone"]
    name: str
    severity: Literal["forbidden", "warning", "allowed"] = "forbidden"
    message: Optional[str] = None
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None
    around_id: Optional[str] = None
    around_target: Optional[Target] = None


class DesignAreaOp(BaseModel):
    """Полный дизайн двора: каркас дорожек и благоустройство вокруг него."""

    op: Literal["design_area"]
    elements: list[str] = []  # пусто -- courtyard_design.DEFAULT_ELEMENTS
    # courtyard_design.STYLES; неизвестное значение и null -- auto.
    style: Optional[str] = "auto"
    tree_ids: list[str] = []
    bush_ids: list[str] = []
    x: Optional[float] = None
    z: Optional[float] = None
    radius_m: Optional[float] = None


class RunGreenPlanOp(BaseModel):
    """Озеленить весь участок GreenPlan (greenplan/pipeline.py) с заданными
    параметрами -- "озелени участок в регулярном стиле с липами". Сам
    GreenPlan запускает фронтенд после применения плана: у него своя панель
    с решениями по зонам, нарушениями и пояснительной запиской. None --
    параметр по умолчанию (GreenPlanOptions)."""

    op: Literal["run_greenplan"]
    style: Optional[Literal["auto", "regular", "landscape"]] = None
    trees: Optional[bool] = None
    bushes: Optional[bool] = None
    lawn: Optional[bool] = None
    paths: Optional[bool] = None
    lighting: Optional[bool] = None
    benches: Optional[bool] = None
    preferred_trees: list[str] = []
    preferred_bushes: list[str] = []


Operation = Annotated[
    AddOp
    | RemoveOp
    | MoveOp
    | RotateOp
    | PlaceAlongOp
    | PlaceInAreaOp
    | RemoveWhereOp
    | ConnectOp
    | CoverAreaOp
    | ReplaceWhereOp
    | ThinOutOp
    | ResizeOp
    | FaceOp
    | AlignAlongOp
    | LineOfOp
    | EncloseOp
    | DuplicateNearOp
    | SetCountOp
    | DefineZoneOp
    | DesignAreaOp
    | RunGreenPlanOp,
    Field(discriminator="op"),
]
_OPERATION = TypeAdapter(Operation)


class LlmPlan(BaseModel):
    # Сырые словари, а не list[Operation]: одна кривая операция не должна
    # ронять весь план -- каждая разбирается отдельно в apply_plan().
    operations: list[dict] = []
    explanation: str = ""


MAX_TURN_IDS = 500


class ChatTurn(BaseModel):
    """Прошлый обмен в чате ассистента: без него модель не понимает отсылок
    вроде "убери их" или "то же самое у второго дома" -- каждый запрос
    иначе приходит к ней как первый."""

    instruction: str = Field(max_length=2000)
    explanation: str = Field(default="", max_length=2000)
    applied: list[str] = Field(default=[], max_length=50)
    # id объектов этой правки: «убери их» -- это они. Длинный список
    # обрезается, а не отклоняется.
    added_ids: list[str] = []

    @field_validator("added_ids")
    @classmethod
    def _cap_ids(cls, ids: list[str]) -> list[str]:
        return ids[:MAX_TURN_IDS]


class TextEditRequest(BaseModel):
    scene: Scene
    instruction: str = Field(min_length=1, max_length=2000)
    # Прошлые правки этого чата, старые первыми; уже применены к scene.
    history: list[ChatTurn] = Field(default=[], max_length=20)


class TextEditResult(BaseModel):
    scene: Scene
    explanation: str
    applied: list[str]
    rejected: list[str]
    warnings: list[str]
    # Параметры GreenPlan, если модель выбрала run_greenplan: фронтенд
    # запускает его на scene этого ответа.
    greenplan: Optional[GreenPlanOptions] = None
    # id созданных объектов -- фронтенд возвращает их в истории чата.
    added_ids: list[str] = []
