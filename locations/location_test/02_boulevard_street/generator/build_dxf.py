# Тестовый участок 02: отрезок городской улицы с бульваром, 2,9 га (260 x 110 м).
#
# Профиль с юга на север: тротуар (5 м) -- полоса озеленения под воздушной ЛЭП
# (4 м) -- проезжая часть в 4 полосы (16 м) -- полоса озеленения (4 м) --
# бульвар (30 м) с аллеей посередине и двумя площадками отдыха -- местный
# проезд (6 м) -- тротуар (4 м) -- палисадники (19 м) и три жилых дома (9, 12
# и 9 этажей) фасадами к улице. Два пешеходных перехода связывают тротуар с
# бульваром и домами.
#
# Сети, как на настоящей улице, идут вдоль неё: водопровод и связь -- под
# южным тротуаром, теплосеть -- под северной кромкой бульвара, канализация --
# под проездом, кабель -- под северным тротуаром, газ -- вдоль тротуара на
# краю палисадников; от магистралей -- вводы в дома поперёк бульвара, проезда
# и палисадников. Над южной полосой озеленения -- воздушная ЛЭП (провод на 9 м).
#
# Существующих деревьев нет -- только кусты сирени в разрывах между домами;
# бульвар и палисадники засаживает GreenPlan с нуля.
#
# Что проверять: рядовые посадки вдоль проезжей части и аллеи, посадка в
# палисадниках с отступом от фасадов, вводы сетей не засажены, под ЛЭП и на
# проезжей части ничего не сажается.
#
# Синтетический участок, без привязки к реальному адресу. Запуск из этой папки:
#   python build_dxf.py
# Результат пишется уровнем выше: ../02_boulevard_street.dxf

import sys
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dxf_kit import Site  # noqa: E402

L = 260  # длина участка вдоль улицы
FACADE = 88  # южные фасады домов; палисадник -- от тротуара (69) до фасада
site = Site()
site.boundary(box(0, 0, L, 110))

CROSSINGS = (60, 200)  # оси пешеходных переходов
PLAZAS = (80, 180)  # центры площадок отдыха на бульваре

# --- Проезжая часть и местный проезд -------------------------------------------
carriageway = box(0, 9, L, 25)
driveway = box(0, 59, L, 65)
site.area(unary_union([carriageway, driveway]), "ROAD")
site.curbs(carriageway)
site.curbs(driveway)
for x in CROSSINGS:
    site.crosswalk(x, 17, width=15, length=6, horizontal=False)
    site.crosswalk(x, 62, width=5.5, length=4, horizontal=False)
# осевая разметка
for x0 in range(0, L, 12):
    site.msp.add_line((x0, 17), (x0 + 6, 17), dxfattribs={"layer": "ROAD_MARKINGS"})

# --- Тротуары, аллея, площадки, проходы через газоны --------------------------
site.area(unary_union([box(0, 0, L, 5), box(0, 65, L, 69)]), "SIDEWALK")
paths = [box(0, 41, L, 47)]  # аллея бульвара
paths += [box(cx - 10, 36, cx + 10, 52) for cx in PLAZAS]
for x in CROSSINGS:
    paths += [box(x - 3, 5, x + 3, 9), box(x - 3, 25, x + 3, 41), box(x - 3, 47, x + 3, 59)]

# --- Дома фасадами к улице -----------------------------------------------------
houses = [
    ("Жилой дом 1", box(15, FACADE, 85, FACADE + 14), 9, (27, 50, 73)),
    ("Жилой дом 2", box(100, FACADE, 160, FACADE + 14), 12, (112, 130, 148)),
    ("Жилой дом 3", box(175, FACADE, 245, FACADE + 14), 9, (187, 210, 233)),
]
for name, fp, levels, doors in houses:
    site.building([fp], levels=levels, name=name, entrances=[(x, FACADE) for x in doors])
    paths += [box(x - 1.25, 69, x + 1.25, FACADE) for x in doors]  # от тротуара к подъездам
site.area(unary_union(paths), "PATHS")

# --- Сети ------------------------------------------------------------------------
site.utility("WATER_SUPPLY_B1", [(0, 2.5), (L, 2.5)])
for x in (50, 130, 210):
    site.utility("WATER_SUPPLY_B1", [(x, 2.5), (x, FACADE)])
site.utility("CABLE_COMM", [(0, 1), (L, 1)])
site.utility("HEATING_T1", [(0, 55), (L, 55)])
for x in (70, 150, 230):
    site.utility("HEATING_T1", [(x, 55), (x, FACADE)])
site.utility("SEWER_K1", [(0, 62), (L, 62)])
for x in (40, 120, 200):
    site.utility("SEWER_K1", [(x, FACADE), (x, 62)])
site.utility("POWER_CABLE", [(0, 67), (L, 67)])
for x in (30, 115, 195):
    site.utility("POWER_CABLE", [(x, 67), (x, FACADE)])
site.utility("GAS_PIPE", [(0, 70.5), (L, 70.5)])
for x in (20, 105, 180):
    site.utility("GAS_PIPE", [(x, 70.5), (x, FACADE)])
site.overhead_line([(0, 7), (L, 7)], height=9.0)

# --- Существующая растительность ----------------------------------------------
# Деревьев нет -- участок засаживается с нуля; только сирень в разрывах между
# домами (x 85..100 и 160..175).
for x, y in ((88, 96), (90, 100), (96, 98), (163, 97), (168, 101), (172, 96)):
    site.plant("BUSH", "Сирень обыкновенная", x, y, crown=1.2)

# --- Освещение, скамейки -----------------------------------------------------------
# Только там, где редактор не подсветит объект нарушением (см. Site.is_clear):
# под ЛЭП и над сетями южного тротуара опор нет, освещение проезжей части --
# с северной полосы озеленения.
for x in range(25, L, 30):
    if site.is_clear(x, 27):
        site.lamp(x, 27, height=9.0)
for i, x in enumerate(range(5, L, 20)):
    y = 40 if i % 2 else 48
    if site.is_clear(x, y):
        site.lamp(x, y)
# Скамейки -- вдоль аллеи по обе стороны от площадок, лицом к аллее.
for cx in PLAZAS:
    for dx in (-16, -13, 13, 16):
        for y, angle in ((40.2, 0), (47.8, 180)):
            if site.is_clear(cx + dx, y):
                site.bench(cx + dx, y, angle)

site.save(str(Path(__file__).resolve().parents[1] / "02_boulevard_street.dxf"))
