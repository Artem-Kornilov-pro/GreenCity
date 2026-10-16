# Тестовый участок 04: тот же двор, что 03_courtyard_renovation, но оформленный
# так, как чертежи присылают заказчики, -- без единых имён слоёв.
#
# Что в нём необычного и что должен сделать сервис:
#   * русские имена и коды сетей СПДС: «Проезды», «Строения», «В1», «К1»,
#     «Т1», «КЛ 0,4 кВ», «Слаботочка»;
#   * топоплан -- внешней ссылкой: слои «Топоплан|Строения», «Топоплан|Проезды»;
#   * существующие деревья и кусты -- блоками с названием породы
#     («Липа_мелколистная») на общем слое «Озеленение»;
#   * газон -- штриховкой с образцом GRASS на слое «Штриховки»;
#   * газопровод -- линиями типа «Г1» на слое «Сети проектные»;
#   * миллиметры, а единицы в заголовке не заданы ($INSUNITS = 0);
#   * трансформаторная подстанция -- на слое «Слой 7», по которому её не
#     узнать: его тип назначается вручную в окне «Слои чертежа».
#
# Ориентир: всё, кроме «Слоя 7», распознаётся само; после назначения «Слой 7»
# -> «Трансформаторная подстанция» сцена и результат GreenPlan -- как у
# 03_courtyard_renovation.
#
# Запуск из этой папки (нужен ezdxf):
#   python build_dxf.py
# Результат пишется уровнем выше: ../04_customer_layers.dxf

from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "03_courtyard_renovation" / "03_courtyard_renovation.dxf"
TARGET = HERE.parent / "04_customer_layers.dxf"

NAMES = {
    "TERRITORY_BOUNDARY": "Граница участка",
    "BUILDING_FOOTPRINT": "Топоплан|Строения",
    "BUILDING_3D": "Топоплан|Строения 3D",
    "ROAD": "Топоплан|Проезды",
    "SIDEWALK": "Тротуары",
    "PATHS": "Пешеходные дорожки",
    "PARKING": "Автостоянка",
    "PARKING_MARKINGS": "Разметка",
    "PLAYGROUND": "Детская площадка",
    "PLAYGROUND_SPORTS": "Спортплощадка",
    "TRANSFORMER_SUBSTATION": "Слой 7",
    "WATER_SUPPLY_B1": "В1",
    "SEWER_K1": "К1",
    "HEATING_T1": "Т1",
    "POWER_CABLE": "КЛ 0,4 кВ",
    "CABLE_COMM": "Слаботочка",
    "LAMPS": "Опоры освещения",
    "BENCHES": "Скамьи",
    "ENTRANCES": "Входы",
    "LABELS": "Подписи",
    "CURB": "Бортовой камень",
}


def main() -> None:
    doc = ezdxf.readfile(SOURCE)
    msp = doc.modelspace()
    doc.linetypes.add("Г1", pattern=[2.0, 1.4, -0.6], description="Газопровод ---Г1---")

    for e in list(msp):
        layer = e.dxf.layer
        if layer.startswith(("EXISTING_TREE_", "EXISTING_BUSH_")) and e.dxftype() == "POINT":
            # Посадка -- вставка блока с названием породы, круг-маркер не нужен.
            species = layer.split("_", 2)[2]
            name = species.replace(" ", "_")
            if name not in doc.blocks:
                doc.blocks.new(name).add_circle((0, 0), 1.5)
            msp.add_blockref(name, e.dxf.location, dxfattribs={"layer": "Озеленение"})
            msp.delete_entity(e)
        elif layer.startswith(("EXISTING_TREE_", "EXISTING_BUSH_")):
            msp.delete_entity(e)
        elif layer == "GRASS" and e.dxftype() == "HATCH":
            e.dxf.layer = "Штриховки"
            e.set_pattern_fill("GRASS", scale=0.5)
        elif layer == "GRASS":
            hatch = msp.add_hatch(dxfattribs={"layer": "Штриховки"})
            hatch.paths.add_polyline_path([(x, y) for x, y, *_ in e.get_points()], is_closed=True)
            hatch.set_pattern_fill("GRASS", scale=0.5)
            msp.delete_entity(e)
        elif layer == "GAS_PIPE":
            e.dxf.layer = "Сети проектные"
            e.dxf.linetype = "Г1"
        else:
            e.dxf.layer = NAMES.get(layer, layer)

    for e in msp:
        if e.dxf.layer not in doc.layers:
            doc.layers.add(e.dxf.layer)
    # Миллиметры без единиц в заголовке.
    for e in msp:
        e.transform(Matrix44.scale(1000, 1000, 1000))
    for block in doc.blocks:
        if not block.name.startswith("*"):
            for e in block:
                e.transform(Matrix44.scale(1000, 1000, 1000))
    doc.header["$INSUNITS"] = 0
    doc.saveas(TARGET)
    print(f"Записано: {TARGET}")


if __name__ == "__main__":
    main()
