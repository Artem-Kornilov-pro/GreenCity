"""greenplan.assortment_report.summarize_assortment -- GreenPlan, Этап 5: сведение
количеств по видам в структуру ассортиментной ведомости. Считает только
собственную генерацию GreenPlan (metadata.generated), не реальную ведомость
заказчика -- см. докстринг модуля."""

from core.plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender
from greenplan.assortment_report import summarize_assortment
from helpers import make_object


def _tree(id_, catalog_id="tree_medium", generated=True):
    metadata = {"catalogId": catalog_id}
    if generated:
        metadata["generated"] = True
    return make_object(id_, "tree", 0, 0, metadata=metadata)


def _catalog():
    return {
        "tree_medium": CatalogItem(
            id="tree_medium", category="tree", label="Дерево — среднее", setback_kind="tree", object_type="tree",
            model="/x.glb", dimensions=CatalogItemDimensions(height=3.0, radius=0.9),
            render=CatalogItemRender(shape="cone", color="#000"),
        ),
        "tree_tall": CatalogItem(
            id="tree_tall", category="tree", label="Дерево — высокое", setback_kind="tree", object_type="tree",
            model="/x.glb", dimensions=CatalogItemDimensions(height=4.6, radius=1.1),
            render=CatalogItemRender(shape="cone", color="#000"),
        ),
        "bush_medium": CatalogItem(
            id="bush_medium", category="bush", label="Кустарник — средний", setback_kind="bush", object_type="bush",
            model="/x.glb", dimensions=CatalogItemDimensions(height=1.0, radius=0.5),
            render=CatalogItemRender(shape="sphere", color="#000"),
        ),
    }


def test_groups_generated_objects_by_category_and_species():
    catalog = _catalog()
    objects = [_tree("t1"), _tree("t2"), _tree("t3", catalog_id="tree_tall")]
    rows = summarize_assortment(objects, catalog)
    by_species = {r.species: r.count for r in rows}
    assert by_species["Дерево — среднее"] == 2
    assert by_species["Дерево — высокое"] == 1
    assert all(r.category == "дерево" for r in rows)


def test_ignores_objects_without_generated_flag():
    catalog = _catalog()
    objects = [_tree("t1", generated=False), _tree("t2", generated=True)]
    rows = summarize_assortment(objects, catalog)
    assert sum(r.count for r in rows) == 1


def test_ignores_objects_with_unknown_catalog_id():
    catalog = _catalog()
    objects = [_tree("t1", catalog_id="does-not-exist")]
    assert summarize_assortment(objects, catalog) == []


def test_mixes_trees_and_bushes_under_different_categories():
    catalog = _catalog()
    objects = [_tree("t1"), make_object("b1", "bush", 1, 1, metadata={"generated": True, "catalogId": "bush_medium"})]
    rows = summarize_assortment(objects, catalog)
    assert {r.category for r in rows} == {"дерево", "кустарник"}


def test_sorted_by_category_then_count_descending():
    catalog = _catalog()
    objects = [_tree("t1"), _tree("t2"), _tree("t3", catalog_id="tree_tall")]
    rows = summarize_assortment(objects, catalog)
    assert rows[0].count >= rows[-1].count


# --- lawn_assortment: газон в ведомости -- в м², только новый ----------------


def _lawn(status, area):
    from core.schemas import LawnArea, Point2

    square = [Point2(x=0, z=0), Point2(x=1, z=0), Point2(x=1, z=1)]
    return LawnArea(id=f"lawn_{status}", polygon=square, area_sqm=area, status=status)


def test_lawn_row_counts_only_new_lawn_in_square_metres():
    from greenplan.assortment_report import lawn_assortment

    [row] = lawn_assortment([_lawn("new", 120.4), _lawn("new", 30.3), _lawn("existing", 5000)])
    assert (row.category, row.species, row.count, row.unit) == ("газон", "Газон обыкновенный", 151, "м²")


def test_no_lawn_row_without_new_lawn():
    from greenplan.assortment_report import lawn_assortment

    assert lawn_assortment([_lawn("existing", 5000)]) == []
    assert lawn_assortment([]) == []


def test_plants_are_counted_in_pieces():
    rows = summarize_assortment([_tree("t1")], _catalog())
    assert rows[0].unit == "шт."
