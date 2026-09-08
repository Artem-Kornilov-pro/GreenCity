"""
Единый источник правды для нормативных отступов посадок -- используется
generate-greenery-алгоритмом (greenery_generator.py). Портировано 1:1 из
frontend/src/setbackNorms.ts (см. докстринг там же про СНиП 2.07.01-89*/
СП 42.13330.2016) -- фронт использует эту же таблицу для проверки при
ручном drag&drop (frontend/src/geometry.ts::checkViolations). Синхронизация
между .ts и .py пока ручная (нет общей кодогенерации схемы, см.
backend/schemas.py) -- при правке таблицы обнови оба файла.

Это ДОПОЛНИТЕЛЬНЫЙ запас поверх уже нарисованной зоны (для труб/кабелей она
уже отбуферена на minDistance из parser/parse_dxf.py -- это охранная зона
самой сети, не связанная с видом посадки). 0 означает "не нормируется".

--- Виды деревьев (species) ---

У разных пород разная агрессивность корневой системы и размер кроны, поэтому
у двух деревьев с одинаковым type="tree" может быть разный требуемый отступ
от одной и той же трубы/здания (например тополю нужно больше места, чем
берёзе). Это учтено на уровне интерфейса setback_for(): опциональный
параметр species проверяется в SPECIES_SETBACK_OVERRIDES ПЕРЕД общей таблицей
SETBACK_NORMS, и если для (species, zone_type) есть запись -- используется
она, иначе -- общая норма для "tree".

Каталог видов сейчас пуст: пока используется один дефолтный вид без
переопределений (см. DEFAULT_TREE_SPECIES), генератор ничем не отличается от
случая "просто дерево". Чтобы добавить реальный вид с другими требованиями --
достаточно дописать запись в SPECIES_SETBACK_OVERRIDES ниже, greenery_generator.py
и checkViolations-подобная проверка трогать не придётся.
"""

from __future__ import annotations

from typing import Literal, Optional

PlantKind = Literal["tree", "bush"]

SETBACK_NORMS: dict[str, dict[PlantKind, float]] = {
    "building": {"tree": 5.0, "bush": 1.5},
    "road": {"tree": 2.0, "bush": 1.0},
    "gas_pipeline": {"tree": 1.5, "bush": 0.0},
    "sewer": {"tree": 1.5, "bush": 0.0},
    "water_pipeline": {"tree": 2.0, "bush": 0.0},
    "electrical": {"tree": 2.0, "bush": 0.7},
    "pedestrian_path": {"tree": 0.7, "bush": 0.5},
}

# species -> {zone_type: distance}. Применяется только для plant_kind=="tree".
# Пример на будущее (закомментирован -- сейчас каталог пуст):
#   "тополь чёрный": {"building": 8.0, "sewer": 3.0, "water_pipeline": 3.0},
#   -- агрессивная поверхностная корневая система, может повредить фундамент
#      и трубы дальше, чем норма для "дерева вообще".
#   "берёза повислая": {},
#   -- корневая система неглубокая, общая норма для tree уже достаточна,
#      явных переопределений не требуется (пустой dict эквивалентен
#      отсутствию записи в каталоге).
SPECIES_SETBACK_OVERRIDES: dict[str, dict[str, float]] = {}

# Вид дерева, используемый генератором, когда конкретный species не задан
# явно вызывающим кодом. Есть только для того, чтобы у сгенерированных
# деревьев было осмысленное metadata.species для UI/панели объекта -- сейчас
# он не даёт никаких переопределений отступов (записи в
# SPECIES_SETBACK_OVERRIDES нет).
DEFAULT_TREE_SPECIES = "Липа мелколистная"


def setback_for(
    zone_type: str,
    plant_kind: PlantKind,
    zone_min_distance: float,
    species: Optional[str] = None,
) -> float:
    """Требуемый отступ данного вида посадки от зоны типа zone_type.

    Порядок разрешения:
    1. Если plant_kind == "tree" и для species есть запись в
       SPECIES_SETBACK_OVERRIDES с этим zone_type -- вернуть её.
    2. Иначе -- общая норма из SETBACK_NORMS для этого zone_type/plant_kind.
    3. Если zone_type вообще не описан в таблице (нестандартная/custom
       зона) -- вернуть minDistance самой зоны как есть, без выдумывания
       цифр, которые нечем подтвердить (тот же принцип, что в .ts-версии).
    """
    if plant_kind == "tree" and species:
        override = SPECIES_SETBACK_OVERRIDES.get(species)
        if override and zone_type in override:
            return override[zone_type]

    rule = SETBACK_NORMS.get(zone_type)
    if rule:
        return rule[plant_kind]
    return zone_min_distance


def plant_kind_of_object_type(object_type: str) -> Optional[PlantKind]:
    if object_type in ("tree", "bush"):
        return object_type  # type: ignore[return-value]
    return None
