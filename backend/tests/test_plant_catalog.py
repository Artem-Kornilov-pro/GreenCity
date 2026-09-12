"""plant_catalog: базовый каталог + необязательный сгенерированный пак
(catalog_generated.json). Файл в этом репозитории реально существует
(200 деревьев из подключённого пака, см. tools/convert_models.mjs) --
поведение "файла нет" проверяем отдельно, через monkeypatch пути."""

import json

import plant_catalog
from plant_catalog import CATALOG, CatalogItem, catalog_by_id, items_by_category, load_catalog


def test_load_catalog_includes_base_catalog():
    catalog = load_catalog()
    base_ids = {item.id for item in CATALOG}
    loaded_ids = {item.id for item in catalog}
    assert base_ids <= loaded_ids


def test_catalog_by_id_maps_every_item_by_its_own_id():
    by_id = catalog_by_id()
    for item in CATALOG:
        assert by_id[item.id] is not None
        assert by_id[item.id].id == item.id


def test_items_by_category_filters_correctly():
    bushes = items_by_category("bush")
    assert bushes  # каталог заведомо содержит кусты (bush_medium и т.п.)
    assert all(item.category == "bush" for item in bushes)

    furniture = items_by_category("furniture")
    assert {"bench", "lamp", "trash", "fountain"} <= {item.id for item in furniture}


def test_base_catalog_has_all_expected_object_types():
    object_types = {item.object_type for item in CATALOG}
    assert {"tree", "bush", "hedge_segment", "lawn_patch", "flowerbed_patch", "path_segment", "bench", "lamp", "trash", "fountain"} <= object_types


def test_missing_generated_file_falls_back_to_base_catalog_only(monkeypatch, tmp_path):
    monkeypatch.setattr(plant_catalog, "_GENERATED_PATH", tmp_path / "does_not_exist.json")
    catalog = load_catalog()
    assert {item.id for item in catalog} == {item.id for item in CATALOG}


def test_corrupt_generated_file_is_ignored_not_fatal(monkeypatch, tmp_path):
    bad_file = tmp_path / "catalog_generated.json"
    bad_file.write_text("{ этот файл не json", encoding="utf-8")
    monkeypatch.setattr(plant_catalog, "_GENERATED_PATH", bad_file)
    catalog = load_catalog()  # не должно поднять исключение
    assert {item.id for item in catalog} == {item.id for item in CATALOG}


def test_generated_file_entries_are_merged_in(monkeypatch, tmp_path):
    extra = {
        "id": "tree_test_extra",
        "category": "tree",
        "label": "Тестовое дерево",
        "setback_kind": "tree",
        "object_type": "tree",
        "model": "/models/tree_test_extra.glb",
        "dimensions": {"height": 3.0, "radius": 1.0},
        "render": {"shape": "cone", "color": "#2e7d3a"},
    }
    generated_file = tmp_path / "catalog_generated.json"
    generated_file.write_text(json.dumps([extra]), encoding="utf-8")
    monkeypatch.setattr(plant_catalog, "_GENERATED_PATH", generated_file)

    catalog = load_catalog()
    ids = {item.id for item in catalog}
    assert "tree_test_extra" in ids
    assert len(catalog) == len(CATALOG) + 1


def test_dimensions_validation_requires_height():
    try:
        CatalogItem(
            id="broken", category="tree", label="broken", object_type="tree", model="x",
            dimensions={}, render={"shape": "cone", "color": "#000"},
        )
    except Exception as e:  # pydantic.ValidationError
        assert "height" in str(e)
    else:
        raise AssertionError("height обязателен в CatalogItemDimensions")
