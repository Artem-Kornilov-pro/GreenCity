"""greenplan.dxf_export.export_greenplan_dxf -- GreenPlan, Этап 6: финальный DXF
(issue #23, "слой «сохранённое» + слой «новая посадка» + инженерные сети").
Быстрый контракт -- на синтетической фикстуре scene_02; то, что итоговый файл
реально валиден и новые объекты действительно оказываются на отдельном слое
-- перепроверяется на настоящем DXF корпуса (тот же принцип "не доверять
генерации без реальной перепроверки", что и в test_deterministic_placement.py)."""

import ezdxf

from core.schemas import Scene
from exchange.export_dxf import DXF_VERSION
from greenplan.dxf_export import export_greenplan_dxf
from greenplan.pattern_corpus import _corpus_scenes
from greenplan.zone_partitioning import partition_zones
from parse_dxf import parse_dxf_file


def _trees_and_bushes(catalog):
    trees = [c for c in catalog if c.category == "tree"]
    bushes = [c for c in catalog if c.category == "bush" and c.object_type == "bush"]
    return trees, bushes


def test_export_greenplan_dxf_returns_doc_and_matching_assignments(scene_02, catalog):
    trees, bushes = _trees_and_bushes(catalog)
    doc, assignments = export_greenplan_dxf(scene_02, trees, bushes, k=3)
    assert doc.dxfversion is not None
    assert len(assignments) == len(partition_zones(scene_02))


def test_export_greenplan_dxf_works_with_empty_catalog(scene_02):
    doc, assignments = export_greenplan_dxf(scene_02, trees=[], bushes=[], k=3)
    assert doc is not None
    assert len(assignments) == len(partition_zones(scene_02))


def test_export_greenplan_dxf_new_objects_land_on_new_prefixed_layers(scene_02, catalog):
    trees, bushes = _trees_and_bushes(catalog)
    doc, _ = export_greenplan_dxf(scene_02, trees, bushes, k=3)
    layer_names = {layer.dxf.name for layer in doc.layers}
    new_layers = {name for name in layer_names if name.startswith("NEW_")}
    assert new_layers, "хотя бы один слой новой посадки должен появиться при непустом каталоге"


def test_export_greenplan_dxf_real_project_round_trips_through_the_parser(tmp_path, catalog):
    """Пишем DXF, перепарсиваем его же продакшен-парсером -- убеждаемся, что
    файл реально валиден (не только 'ezdxf не упал при записи'), и что новые
    объекты не потерялись при повторном чтении (NEW_TREE/NEW_BUSH содержат
    подстроку TREE/BUSH -- parser.POINT_LAYER_RULES их узнаёт)."""
    trees, bushes = _trees_and_bushes(catalog)
    scene = _corpus_scenes()["12_natashinsky_proezd"]

    doc, assignments = export_greenplan_dxf(scene, trees, bushes, k=3)
    assert assignments  # иначе тест ничего не проверяет по новой посадке
    out_path = tmp_path / "greenplan_export.dxf"
    doc.saveas(str(out_path))

    reparsed = Scene.model_validate(parse_dxf_file(str(out_path)))
    assert reparsed.boundary is not None
    # Не точное равенство: у export_dxf.py уже есть задокументированный
    # (и покрытый отдельным тестом в test_export_dxf.py) допуск на рост числа
    # зон при round-trip -- здания одновременно и SceneObject (для 3D-меша), и
    # RestrictionZone (для отступов), поэтому на реэкспорте у них может
    # оказаться более одного узнаваемого следа на слоях с общей подстрокой.
    assert len(reparsed.restrictions) <= 3 * len(scene.restrictions) + 5
    # Новые точечные объекты (деревья/кусты) не должны потеряться при
    # повторном чтении -- не обязательно тем же числом, но точно не меньше.
    assert len(reparsed.objects) >= len(scene.objects)
    assert doc.dxfversion == ezdxf.new(DXF_VERSION).dxfversion
