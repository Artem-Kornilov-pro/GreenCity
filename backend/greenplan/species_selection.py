"""
Подбор видов растений для GreenPlan (issue #59): какие деревья и кустарники
ставить в конкретную зону конкретным паттерном. Раньше deterministic_placement
перебирал по кругу ВЕСЬ каталог (129 деревьев, 127 кустов), включая базовые
модели ("Дерево -- среднее") и записи-заглушки, разобранные из примечаний
("визуально характерна для городского озеленения"). Итоговая ведомость
выглядела как случайный набор.

Теперь -- три фильтра по нормативным документам и затем выбор по форме:

1. Вид есть в ассортименте для озеленения Москвы (data/norms/assortment-msk,
   документ заказчика) и отмечен "+" для типа территории участка
   (site_characterization.territory_type). Базовые модели и заглушки туда не
   попадают по определению -- у них нет названия вида.
2. Вид не в перечне инвазивных ППМ 369-ПП (invasive_species.py; каталог их
   уже не содержит, здесь -- повторная проверка на случай чужого каталога).
3. Ограничения по кодам ассортимента и СП 82.13330.2016, п. 9.22 -- см.
   _excluded_reason.

Из оставшихся для каждой РОЛИ паттерна (живая изгородь, аллея, роща...)
берутся виды подходящей формы (model_group из
data/plant_archetypes/species_catalog.json -- морфологический архетип вида, а
не габарит 3D-модели) в заданном количестве. Порядок: сначала базовый
ассортимент 515-ПП (для дворов), затем "основной" ассортимент Москвы, затем
"дополнительный", затем "перспективный"; внутри -- стабильный хеш от участка,
чтобы разные участки получали разные виды, а один и тот же участок -- всегда
одни и те же.

Выбор делается на весь участок для пары (вид зоны, паттерн), а не на каждую
зону отдельно: живая изгородь вдоль всех дорожек участка -- из одного вида,
как это и проектируют (и как это пересказывает отчёт о решениях).

Если ни один вид переданного каталога не прошёл фильтры (например, тестовый
каталог из синтетических записей) -- используется переданный набор как есть,
и это честно помечено в basis: "вне ассортимента".
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel

from core.invasive_species import invasive_match, normalize_species_name
from core.paths import DATA_DIR, NORMS_DIR
from core.plant_catalog import CatalogItem
from core.schemas import Scene
from core.setback_norms import THORNY_GENERA, genus_of, setback_for
from greenplan.site_characterization import TerritoryType
from greenplan.zone_partitioning import ZoneKind

_ASSORTMENT_DIR = NORMS_DIR / "assortment-msk"
_ASSORTMENT_515 = NORMS_DIR / "515-pp" / "assortment_base.csv"
_SPECIES_CATALOG = DATA_DIR / "plant_archetypes" / "species_catalog.json"

# Тип территории участка -> колонка ассортимента Москвы (восемь категорий
# документа, из них по геометрии сцены различимы пять, см.
# site_characterization.py). "неопределено" -- дворовые территории: самая
# частая категория городских участков; в basis это видно.
TERRITORY_COLUMN: dict[TerritoryType, str] = {
    "двор": "Дворовые территории",
    "улица": (
        "Магистрали (в т.ч. шоссе, проспекты, улицы с высокоскоростным движением, кольцевые дороги и хорды) "
        "*Внутриквартальные улицы и проезды местного значения"
    ),
    "площадь": (
        "Площади, пространства общественно-делового и торгово-развлекательного назначения "
        "(запечатанные и полузапечатанные поверхности)"
    ),
    "парк_сквер": "Парки *Бульвары, скверы, набережные, сады",
    "промышленная_охранная": (
        "Территории производственного назначения *Озелененные территории охранных и санитарно-защитных зон"
    ),
    "неопределено": "Дворовые территории",
}
TERRITORY_LABEL: dict[TerritoryType, str] = {
    "двор": "дворовые территории",
    "улица": "магистрали и улицы",
    "площадь": "площади",
    "парк_сквер": "парки, бульвары, скверы",
    "промышленная_охранная": "производственные и охранные зоны",
    "неопределено": "дворовые территории (тип участка не определён)",
}

# Порядок предпочтения классов ассортимента (меньше -- раньше).
_CLASS_RANK = {"Основной": 1, "Дополнительный": 2, "Перспективный": 3}


class Role(BaseModel):
    """Что сажать в одной роли паттерна: подходящие морфологические группы
    (в порядке предпочтения) и сколько разных видов взять."""

    groups: tuple[str, ...]
    count: int


# Морфологические группы (model_group из species_catalog.json).
_ALLEY_TREES = ("tree_round", "tree_oval", "tree_columnar")
_COMPACT_TREES = ("tree_columnar", "tree_small_ornamental")
_ORNAMENTAL_TREES = ("tree_small_ornamental", "tree_round", "tree_oval")
_GROVE_TREES = ("tree_round", "tree_oval", "tree_birch", "conifer_spruce", "conifer_pine", "conifer_larch", "conifer_fir")
_HEDGE_SHRUBS = ("shrub_dense_round", "shrub_round", "shrub_spirea_arching", "shrub_silver", "shrub_juniper_upright")
_LOW_SHRUBS = (
    "shrub_low_spreading", "shrub_spirea_arching", "shrub_hydrangea", "shrub_round",
    "shrub_juniper_spreading", "shrub_conifer_low",
)
_MIXED_SHRUBS = ("shrub_round", "shrub_dense_round", "shrub_tall", "shrub_large_multistem", "shrub_silver", "shrub_spirea_arching")
_ANY_TREE = (*_GROVE_TREES, "tree_small_ornamental", "tree_columnar", "tree_poplar", "tree_willow", "tree_weeping")
_ANY_SHRUB = (*_MIXED_SHRUBS, *_LOW_SHRUBS, "shrub_dogwood", "shrub_rose", "shrub_rhododendron", "shrub_mountain_pine")

# Паттерн -> (роль деревьев, роль кустарников). Количество видов -- по
# смыслу приёма: аллея -- однопородная (743-ПП допускает "однопородными или
# смешанными", берём первое как более узнаваемый приём), изгородь из одного
# вида -- стандартная практика, формальный боскет -- одна порода, роща и
# свободная посадка -- смесь (у рощи по одному виду на группу, см.
# deterministic_placement._place_clustered).
PATTERN_ROLES: dict[str, tuple[Role, Role]] = {
    "linear_hedge_row": (Role(groups=_ALLEY_TREES, count=1), Role(groups=_HEDGE_SHRUBS, count=1)),
    "building_ring": (Role(groups=_COMPACT_TREES, count=1), Role(groups=_LOW_SHRUBS, count=2)),
    "diagonal_rows": (Role(groups=_ORNAMENTAL_TREES, count=1), Role(groups=_MIXED_SHRUBS, count=2)),
    "flowing_rows": (Role(groups=_ORNAMENTAL_TREES, count=2), Role(groups=_MIXED_SHRUBS, count=3)),
    "formal_bosque_grid": (Role(groups=("tree_round", "tree_oval"), count=1), Role(groups=_LOW_SHRUBS, count=1)),
    "concentric_rings": (Role(groups=("tree_birch", "tree_columnar", "tree_oval"), count=2), Role(groups=_LOW_SHRUBS, count=1)),
    "triangular_grid_fill": (Role(groups=("tree_oval", "tree_round", "conifer_spruce"), count=1), Role(groups=_HEDGE_SHRUBS, count=1)),
    "grove_clusters": (Role(groups=_GROVE_TREES, count=3), Role(groups=_MIXED_SHRUBS, count=2)),
    "poisson_scatter_fill": (Role(groups=_ANY_TREE, count=3), Role(groups=_MIXED_SHRUBS, count=3)),
    "generic_fill": (Role(groups=_ANY_TREE, count=3), Role(groups=_ANY_SHRUB, count=3)),
}


class AssortmentEntry(BaseModel):
    name: str
    assortment_class: str
    codes: frozenset[str]
    territories: dict[str, str]


class SpeciesPalette(BaseModel):
    trees: list[CatalogItem]
    bushes: list[CatalogItem]
    basis: str


def _expand_names(raw: str) -> list[str]:
    """Название строки ассортимента -> все её варианты: "Липа
    мелколистная/сердцевидная (формы и сорта)" -> ["липа мелколистная",
    "липа сердцевидная"], "Слива колючая/терн" -> ["слива колючая", "терн"]."""
    base = " ".join(re.sub(r"\(.*?\)", " ", raw).split())
    parts = [p.strip() for p in base.split("/") if p.strip()]
    if not parts:
        return []
    first_words = parts[0].split()
    names = [normalize_species_name(parts[0])]
    for part in parts[1:]:
        # Синоним одним словом-прилагательным при двухсловном первом названии
        # -- другой видовой эпитет того же рода ("мелколистная/сердцевидная",
        # "гиннала/приречный"). Существительное -- самостоятельное название
        # ("Слива колючая/терн", "Тополь дрожащий/осина").
        if len(part.split()) == 1 and len(first_words) >= 2 and part.lower().endswith(_ADJECTIVE_ENDINGS):
            names.append(normalize_species_name(f"{first_words[0]} {part}"))
        else:
            names.append(normalize_species_name(part))
    return names


_ADJECTIVE_ENDINGS = ("ая", "яя", "ый", "ий", "ой", "ое", "ее", "ые", "ие")


@lru_cache(maxsize=1)
def _assortment() -> dict[str, AssortmentEntry]:
    """Нормализованное название -> строка ассортимента Москвы (основной,
    дополнительный и перспективный). Первое вхождение побеждает: у
    боярышника, например, две строки (как дерево и как кустарник) с
    одинаковыми отметками."""
    index: dict[str, AssortmentEntry] = {}
    for filename, forced_class in (("main_assortment.csv", None), ("perspective_assortment.csv", "Перспективный")):
        with (_ASSORTMENT_DIR / filename).open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                territories = {col: row[col] for col in row if col in TERRITORY_COLUMN.values()}
                entry = AssortmentEntry(
                    name=row["name"],
                    assortment_class=forced_class or row["assortment_class"],
                    codes=frozenset(code.strip() for code in re.split(r"[;, ]+", row["codes"] or "") if code.strip()),
                    territories=territories,
                )
                for name in _expand_names(row["name"]):
                    index.setdefault(name, entry)
    return index


@lru_cache(maxsize=1)
def _base_515() -> tuple[frozenset[str], frozenset[str]]:
    """(названия видов, роды "(различные виды)") базового ассортимента
    ППМ 515-ПП, табл. 4 -- проверенный набор для жилой застройки."""
    names, genera = set(), set()
    with _ASSORTMENT_515.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["kind"] not in ("tree", "bush"):
                continue
            if "различные виды" in row["name"]:
                genera.add(genus_of(row["name"]))
            else:
                names.update(_expand_names(row["name"]))
    # "Береза бородавчатая" в 515-ПП -- синоним березы повислой (Betula pendula).
    if "береза бородавчатая" in names:
        names.add("береза повислая")
    return frozenset(names), frozenset(genera)


@lru_cache(maxsize=1)
def _model_groups() -> dict[str, str]:
    raw = json.loads(_SPECIES_CATALOG.read_text(encoding="utf-8"))
    return {normalize_species_name(entry["name"]): entry["model_group"] for entry in raw}


def _names_of(item: CatalogItem) -> list[str]:
    # В каталоге бывают составные подписи ("Береза повислая / плакучая").
    return _expand_names(item.label)


def assortment_entry(item: CatalogItem) -> Optional[AssortmentEntry]:
    index = _assortment()
    for name in _names_of(item):
        if name in index:
            return index[name]
    return None


def model_group(item: CatalogItem) -> Optional[str]:
    groups = _model_groups()
    for name in _names_of(item):
        if name in groups:
            return groups[name]
    return None


def in_base_515(item: CatalogItem) -> bool:
    names, genera = _base_515()
    return any(name in names for name in _names_of(item)) or genus_of(item.label) in genera


def site_key(scene: Scene) -> str:
    """Стабильный ключ участка для выбора среди равноценных видов (см.
    _pick): один и тот же участок -- те же виды, разные участки -- разные.
    Контур границы с округлением до дециметра, чтобы float-шум парсера не
    менял выбор."""
    if scene.boundary is None:
        return "no-boundary"
    return ";".join(f"{p.x:.1f},{p.z:.1f}" for p in scene.boundary.polygon)


class SiteContext(BaseModel):
    """То, что влияет на подбор видов на всём участке."""

    territory_type: TerritoryType
    has_playground: bool
    site_key: str  # для стабильного выбора внутри равноценных видов


def _excluded_reason(item: CatalogItem, entry: AssortmentEntry, zone_kind: ZoneKind, site: SiteContext) -> Optional[str]:
    """Почему вид нельзя в эту зону этого участка, или None.

    Коды ассортимента Москвы (см. data/norms/assortment-msk/README.md):
    2 -- не высаживать вблизи детских и спортивных площадок; 5 -- чувствителен
    к противогололёдным реагентам и выхлопам; 7 -- самосев, посадка только
    вдали от ООЗТ (границ ООЗТ в DXF нет -- проверить условие нельзя, поэтому
    не сажаем). Коды 3, 4, 6 касаются ухода, почв и соседства с плодовыми --
    данных для них нет, на выбор они не влияют.
    """
    if invasive_match(item.label):
        return "инвазивный вид (ППМ 369-ПП)"
    if "7" in entry.codes:
        return "код 7: самосев, посадка только вдали от ООЗТ"
    if "2" in entry.codes and site.has_playground:
        return "код 2: не высаживать вблизи детских площадок"
    if zone_kind == "path_corridor" and "5" in entry.codes:
        return "код 5: чувствителен к реагентам и выхлопам -- не у дорожек и проездов"
    # СП 82.13330.2016, п. 9.22: колючие -- не ближе 2 м от площадок и
    # пешеходных коммуникаций. Полоса вдоль дорожки -- ровно такое место.
    if zone_kind == "path_corridor" and genus_of(item.label) in THORNY_GENERA:
        return "колючий вид (СП 82.13330.2016, п. 9.22) -- не вдоль дорожек"
    # Полоса у здания (zone_partitioning.BUILDING_BORDER_BAND_M) начинается
    # сразу за табличным отступом от стены. Виду, которому по породе нужно
    # больше (широкая крона -- 10 м по 743-ПП), в ней места нет вовсе.
    if zone_kind == "building_border" and item.setback_kind in ("tree", "bush"):
        own = setback_for("building", item.setback_kind, 0.0, species=item.label)
        if own > setback_for("building", item.setback_kind, 0.0):
            return f"отступ от здания по породе {own:.0f} м -- не помещается в полосу у здания"
    return None


def _eligible(items: list[CatalogItem], zone_kind: ZoneKind, site: SiteContext) -> list[tuple[CatalogItem, AssortmentEntry]]:
    column = TERRITORY_COLUMN[site.territory_type]
    result = []
    for item in items:
        entry = assortment_entry(item)
        if entry is None or entry.territories.get(column) != "+":
            continue
        if model_group(item) is None or model_group(item).startswith("vine_"):
            continue  # лиана без опоры -- не посадка на газоне
        if _excluded_reason(item, entry, zone_kind, site) is None:
            result.append((item, entry))
    return result


def _stable_hash(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")


def _pick(
    candidates: list[tuple[CatalogItem, AssortmentEntry]], role: Role, site: SiteContext, role_key: str
) -> list[CatalogItem]:
    """До role.count видов: сначала из самой предпочтительной группы формы,
    при нехватке -- из следующих; внутри -- базовый ассортимент 515-ПП (для
    двора), класс ассортимента, затем стабильный хеш участка."""
    prefer_515 = site.territory_type in ("двор", "неопределено")

    def rank(pair: tuple[CatalogItem, AssortmentEntry]) -> tuple:
        item, entry = pair
        group = model_group(item)
        return (
            role.groups.index(group),
            0 if prefer_515 and in_base_515(item) else 1,
            _CLASS_RANK.get(entry.assortment_class, 9),
            _stable_hash(site.site_key, role_key, item.label),
        )

    fitting = [pair[0] for pair in sorted((p for p in candidates if model_group(p[0]) in role.groups), key=rank)]
    chosen: list[CatalogItem] = []
    # Сначала -- разные роды: смесь из трёх жимолостей или двух лип
    # разнообразием только называется. Род повторяется, лишь если разных
    # родов подходящей формы не хватило.
    for distinct_genus in (True, False):
        for item in fitting:
            if len(chosen) >= role.count:
                return chosen
            if any(item.label == other.label for other in chosen):
                continue
            if distinct_genus and any(genus_of(item.label) == genus_of(other.label) for other in chosen):
                continue
            chosen.append(item)
    return chosen


def select_species(
    zone_kind: ZoneKind,
    pattern_id: str,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
    site: SiteContext,
) -> SpeciesPalette:
    """Виды для пары (вид зоны, паттерн) на этом участке."""
    tree_role, bush_role = PATTERN_ROLES[pattern_id]
    chosen_trees = _pick(_eligible(trees, zone_kind, site), tree_role, site, f"{zone_kind}:{pattern_id}:tree")
    chosen_bushes = _pick(_eligible(bushes, zone_kind, site), bush_role, site, f"{zone_kind}:{pattern_id}:bush")

    if not chosen_trees and not chosen_bushes and (trees or bushes):
        # Ни один вид переданного каталога не прошёл фильтры -- работаем тем,
        # что дал вызывающий код, но не выдаём это за подбор по ассортименту.
        return SpeciesPalette(
            trees=trees,
            bushes=bushes,
            basis="вне ассортимента Москвы: в каталоге нет подходящих видов, использован весь переданный набор",
        )

    parts = [f"ассортимент для озеленения Москвы, категория «{TERRITORY_LABEL[site.territory_type]}»"]
    if site.territory_type in ("двор", "неопределено") and any(in_base_515(i) for i in (*chosen_trees, *chosen_bushes)):
        parts.append("в приоритете базовый ассортимент ППМ 515-ПП")
    parts.append("без инвазивных видов ППМ 369-ПП")
    return SpeciesPalette(trees=chosen_trees, bushes=chosen_bushes, basis="; ".join(parts))


def courtyard_palette(scene: Scene, trees: list[CatalogItem], bushes: list[CatalogItem]) -> SpeciesPalette:
    """Виды по умолчанию для дизайна двора в ИИ-редакторе (design_area), когда
    модель не назвала их сама: тот же подбор, что у GreenPlan, для дворовой
    территории. Деревья design_area разбрасывает по двору -- роль свободной
    посадки на открытой зоне; кусты сажает рядами вдоль дорожек -- роль
    изгороди в полосе у дорожки (без колючих и чувствительных к реагентам)."""
    site = SiteContext(
        territory_type="двор",
        has_playground=any(zone.type == "playground_zone" for zone in scene.restrictions),
        site_key=site_key(scene),
    )
    tree_palette = select_species("open_area", "poisson_scatter_fill", trees, [], site)
    bush_palette = select_species("path_corridor", "linear_hedge_row", [], bushes, site)
    return SpeciesPalette(trees=tree_palette.trees, bushes=bush_palette.bushes, basis=tree_palette.basis)
