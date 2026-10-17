"""dxf_parsing/layer_names.py и dxf_parsing/encoding.py -- распознавание слоёв,
названных как угодно: синонимы, коды сетей, внешние ссылки, смешанные
алфавиты, кракозябры, ложные совпадения, назначения пользователя и
распознавание сущности по содержимому."""

import pytest

from dxf_parsing.encoding import repair_mojibake
from dxf_parsing.layer_names import IGNORE_KEY, LAYER_KINDS, LayerResolver, describe_key, is_known
from dxf_parsing.rules import layer_matches


def kind_of(name, layer_map=None):
    key, _ = LayerResolver(layer_map).resolve_layer(name)
    return describe_key(key)[0]


# --- классы и их ключи ----------------------------------------------------------


@pytest.mark.parametrize("kind", [k for k in LAYER_KINDS if k != "ignore"])
def test_every_kind_key_is_recognized_by_the_rules_as_that_kind(kind):
    key = LAYER_KINDS[kind][0]
    assert is_known(key)
    assert describe_key(key)[0] == kind


def test_ignore_key_matches_no_rule():
    assert not is_known(IGNORE_KEY)
    assert describe_key(IGNORE_KEY)[0] == "ignore"


# --- прежние имена: ровно прежнее поведение ------------------------------------

# Слои реальных проектов и эталонов корпуса (locations/): ключ -- само имя,
# то есть правила видят его так же, как до появления синонимов.
CORPUS_LAYERS = [
    "BOUNDARY", "TERRITORY_BOUNDARY", "BUILDING", "BUILDING_3D", "BUILDING_FOOTPRINT", "BUSH", "TREE", "CURB",
    "ELECTR_CABLE", "ELECTR_CABLE_COMM", "GAS", "GAS_PIPE", "GRASS", "GROUND", "HEAT", "HEATING_T1", "LAMPS",
    "OVERHEAD_POWER_LINE", "POWER_LINE_OVERHEAD", "ROAD", "SEWER", "SEWER_K1", "SIDEWALK_PATH",
    "WATER_PIPELINE", "WATER_SUPPLY_B1", "PARKING_MARKINGS", "SPORTS_GROUND", "EXISTING_TREE_Липа мелколистная",
    "! ПР СПИРЕЯ ВАНГУТТА", "(ГП) Привязка дер и куст", "ДВ_ГП_П_Газон_Рулонный", "Граница улицы", "Здания",
    "Части зданий", "Кабель связи", "Подземные коммуникации", "Леса и газоны", "Фонари", "ЛЭП",
]


@pytest.mark.parametrize("name", CORPUS_LAYERS)
def test_layers_known_before_keep_their_name_as_the_key(name):
    key, source = LayerResolver().resolve_layer(name)
    assert source == "name"
    assert key == name.upper()


# --- синонимы ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, kind",
    [
        ("Строения", "building"),
        ("Жилые дома", "building"),
        ("Тепловой пункт", "building"),
        ("Bldg", "building"),
        ("Граница участка", "boundary"),
        ("Граница ЗУ", "boundary"),
        ("Граница", "boundary"),
        ("Plot", "boundary"),
        ("Проезды", "road"),
        ("Проезжая часть", "road"),
        ("Streets", "road"),
        ("Пешеходные дорожки", "path"),
        ("Дорожно-тропиночная сеть", "path"),
        ("Footway", "path"),
        ("Автостоянка", "parking"),
        ("Детская площадка", "playground"),
        ("Травяной покров", "lawn"),
        ("Turf", "lawn"),
        ("Газ", "gas"),
        ("Газ_НД", "gas"),
        ("Г1", "gas"),
        ("Г", "gas"),
        ("К1", "sewer"),
        ("Ливневка", "sewer"),
        ("Storm Drain", "sewer"),
        ("В1", "water"),
        ("B1", "water"),
        ("ХВС", "water"),
        ("Водоснабжение", "water"),
        ("Тепловые сети", "heat"),
        ("Т1", "heat"),
        ("ГВС", "heat"),
        ("Эл.сети", "electrical"),
        ("КЛ 0,4 кВ", "electrical"),
        ("Слаботочные сети", "signal_cable"),
        ("Телефонная канализация", "signal_cable"),
        ("Telecom", "signal_cable"),
        ("ВЛ 10кВ", "overhead_power_line"),
        ("Инженерные сети", "underground"),
        ("Сети", "underground"),
        ("ТП", "transformer"),
        ("КТП-123", "transformer"),
        ("Сущ_дер", "tree"),
        ("Хвойные", "tree"),
        ("Липы", "tree"),
        ("Ели", "tree"),
        ("Живая изгородь", "bush"),
        ("Спирея", "bush"),
        ("Светильники", "lamp"),
        ("Наружное освещение", "lamp"),
        ("StreetLights", "lamp"),
        ("Лавки", "bench"),
        ("Мусорные контейнеры", "urn"),
        ("Бортовой камень", "curb"),
        ("Разметка парковки", "skip"),
        ("Подписи", "skip"),
    ],
)
def test_synonyms(name, kind):
    assert kind_of(name) == kind


@pytest.mark.parametrize(
    "name, kind",
    [
        ("Топоплан|Здания", "building"),  # внешняя ссылка
        ("ТОПО$0$Газон", "lawn"),  # привязанная внешняя ссылка
        ("Гaзoн", "lawn"),  # латинские a и o в русском слове
        ("Бepeзa", "tree"),  # латиницы больше, но Б и З -- только кириллица
        ("ExistingTrees", "tree"),  # CamelCase
        ("Граница_работ", "boundary"),  # подчёркивание вместо пробела ключевого слова
        ("Ð—Ð´Ð°Ð½Ð¸Ñ\udc8f", "building"),  # «Здания» в UTF-8, прочитанные как cp1252
        ("Çäàíèÿ", "building"),  # «Здания» в cp1251, прочитанные как cp1252
    ],
)
def test_normalized_names(name, kind):
    assert kind_of(name) == kind


# --- ложные совпадения прежних правил -----------------------------------------


@pytest.mark.parametrize(
    "name, kind",
    [
        ("STREET_LIGHTS", "lamp"),  # STREET содержит TREE
        ("SITE_TREES", "tree"),  # SITE -- не граница, если слой -- деревья
        ("ROAD_BOUNDARY", "road"),
        ("ROAD_TURN", "road"),  # TURN содержит URN: урн со слоя дороги не берётся
        ("Деревянный настил", None),  # ДЕРЕВЯННЫЙ содержит ДЕРЕВ
        ("Дорожка к дому", "path"),  # «к» -- предлог, а не канализация К
        ("Детский сад", "playground"),
    ],
)
def test_false_matches_are_not_taken(name, kind):
    assert kind_of(name) == kind


def test_masked_word_does_not_give_a_point_object():
    from dxf_parsing.layer_names import _point_rule

    assert _point_rule(LayerResolver().layer_key("ROAD_TURN")) is None
    assert _point_rule(LayerResolver().layer_key("STREET_LIGHTS"))["type"] == "lamp"


def test_unrecognized_names_stay_unrecognized():
    resolver = LayerResolver()
    for name in ("0", "Defpoints", "Слой1", "Ограждение"):
        assert resolver.resolve_layer(name) == (None, None)
        assert resolver.layer_key(name) == name


# --- топосъёмка Мосгеотреста (тайлы tp/up/brd датасета) --------------------------


@pytest.mark.parametrize(
    "name,kind",
    [
        ("Граница заказа", "boundary"),
        ("Трубопроводы", "underground"),
        ("Столбы", "lamp"),
        ("Рамки", "skip"),
        ("Координатные кресты", "skip"),
        ("Колодцы", "topo"),
        ("Люки", "topo"),
        ("Ограды", "topo"),
        ("Откосы", "topo"),
        ("Светофоры", "topo"),
        ("Полоса деревьев", "topo"),
        ("Граница растительности и грунта", "topo"),
        ("Внутреннее заполнение", "topo"),
        ("Территории", "topo"),
        ("Выгребные ямы", "topo"),
    ],
)
def test_mosgeotrest_survey_layers(name, kind):
    assert kind_of(name) == kind


@pytest.mark.parametrize("name", ["_за границами работ", "Вне границ участка", "Граница улицы", "Граница площадки"])
def test_outside_and_other_borders_are_not_the_site_boundary(name):
    assert kind_of(name) != "boundary"


# --- назначения пользователя ----------------------------------------------------


def test_user_assignment_wins_over_recognition():
    resolver = LayerResolver({"Слой 5": "building", "Газон": "ignore"})
    assert resolver.resolve_layer("Слой 5") == ("BUILDING", "override")
    key, source = resolver.resolve_layer("Газон")
    assert (key, source) == (IGNORE_KEY, "override")
    assert not is_known(key)


def test_unknown_kinds_in_layer_map_are_dropped():
    assert LayerResolver({"Слой 5": "skyscraper"}).layer_map == {}


# --- сущность по содержимому ---------------------------------------------------


def test_insert_on_unknown_layer_is_recognized_by_block_name(empty_doc):
    block = empty_doc.blocks.new("Липа_мелколистная")
    block.add_circle((0, 0), 2)
    insert = empty_doc.modelspace().add_blockref("Липа_мелколистная", (5, 5), dxfattribs={"layer": "Озеленение_проект"})
    key = LayerResolver().key(insert)
    assert layer_matches(key, ["ЛИПА МЕЛКОЛИСТНАЯ"])


def test_insert_on_tree_layer_keeps_the_layer_class_whatever_the_block(empty_doc):
    empty_doc.blocks.new("Фонарь")
    insert = empty_doc.modelspace().add_blockref("Фонарь", (0, 0), dxfattribs={"layer": "Деревья"})
    assert LayerResolver().key(insert) == "ДЕРЕВЬЯ"


def test_hatch_on_unknown_layer_is_lawn_by_grass_pattern(empty_doc):
    hatch = empty_doc.modelspace().add_hatch(dxfattribs={"layer": "Штриховки"})
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10)])
    hatch.set_pattern_fill("GRASS")
    assert describe_key(LayerResolver().key(hatch))[0] == "lawn"


def test_line_on_unknown_layer_is_a_network_by_its_linetype(empty_doc):
    empty_doc.linetypes.add("В1", pattern=[1.0, 0.5, -0.5], description="Водопровод")
    line = empty_doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "Сети проектные", "linetype": "В1"})
    assert describe_key(LayerResolver().key(line))[0] == "water"


def test_line_with_a_standard_linetype_stays_unrecognized(empty_doc):
    empty_doc.linetypes.add("DASHED", pattern=[1.0, 0.5, -0.5])
    line = empty_doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "Слой1", "linetype": "DASHED"})
    assert LayerResolver().key(line) == "Слой1"


def test_layer_linetype_refines_a_generic_network_layer(empty_doc):
    empty_doc.linetypes.add("ГАЗОПРОВОД", pattern=[1.0, 0.5, -0.5])
    empty_doc.layers.add("Инженерные сети", linetype="ГАЗОПРОВОД")
    line = empty_doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "Инженерные сети"})
    assert describe_key(LayerResolver().key(line))[0] == "gas"


# --- кракозябры ----------------------------------------------------------------


def test_repair_mojibake_keeps_normal_names():
    for name in ("Здания", "BUILDING", "Bâtiment", "Straße"):
        assert repair_mojibake(name) == name


def test_repair_mojibake_decodes_cad_unicode_escapes():
    assert repair_mojibake("\\U+0413\\U+0430\\U+0437\\U+043e\\U+043d_1") == "Газон_1"
    assert describe_key(LayerResolver().layer_key("\\U+0413\\U+0430\\U+0437"))[0] == "gas"


def test_repair_mojibake_restores_utf8_and_cp1251():
    assert repair_mojibake("Здания".encode().decode("cp1252", errors="surrogateescape")) == "Здания"
    assert repair_mojibake("Газон".encode("cp1251").decode("cp1252")) == "Газон"
