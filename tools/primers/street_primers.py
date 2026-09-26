"""
Эталоны 31-40: фрагменты городских улиц с жилыми домами и подъездами --
типовые случаи, с которыми GreenPlan встречается чаще всего: газонная
полоса с деревьями вдоль проезжей части, тротуар, дома с подъездами и
дорожками к ним, палисадники, дворы за домами, сети под тротуарами.
Собираются вместе с 21-30 (build_primers.py).
"""

from __future__ import annotations

from primer_kit import (
    BEREZA,
    CHEREMUHA,
    DEREN,
    EL,
    GORTENZIYA,
    KIZILNIK,
    KLEN,
    LIPA,
    LISTVENNICA,
    SIREN,
    SP_BUMALDA,
    SP_GRAY,
    SP_JAPAN,
    SP_VANGUTTA,
    TUYA,
    YABLONYA,
    Primer,
    along,
    arc_band,
    circle,
    facade_shrubs,
    hedge,
    hedge_with_gaps,
    house,
    rect,
    strip,
)


def street_south(p: Primer, w: float, road: float = 7.0, green: float = 4.0, sidewalk: float = 3.0,
                 tree: str = LIPA, step: float = 8.0, name: str = "") -> float:
    """Улица вдоль южной границы: проезжая часть, газонная полоса с рядом
    деревьев (2,3 м от кромки -- норма 2 м), тротуар с водопроводом под
    ним, фонари в полосе посередине между деревьями (4 м до ствола).
    Возвращает y внешнего края тротуара."""
    y_green, y_side = road + green, road + green + sidewalk
    p.zone(f"ROAD_STREET{name}", rect(0, 0, w, road))
    p.zone(f"PATH_SIDEWALK{name}", rect(0, y_green, w, y_side))
    p.zone(f"WATER_SUPPLY{name}", rect(0, y_green + 0.8, w, y_green + 2.2))
    p.curb([(0, road), (w, road)])
    p.curb([(0, y_green), (w, y_green)])
    for i, x in enumerate(range(int(step / 2), int(w), int(step))):
        p.plant(tree, x, road + 2.3)
        if i % 3 == 1:
            p.put("lamp", x + step / 2, road + 2.3)
    return y_side


def yard_trees(p: Primer, x0: float, y0: float, x1: float, y1: float, species: list[str], step: float = 6.0) -> None:
    """Деревья по свободной площади двора сеткой со сдвигом рядов; всё,
    что ближе нормы к домам, дорожкам и сетям, отсеивает проверка."""
    j = 0
    y = y0
    while y <= y1:
        x = x0 + (step / 2 if j % 2 else 0.0)
        i = 0
        while x <= x1:
            p.plant(species[(i + j) % len(species)], x, y)
            x += step
            i += 1
        y += step
        j += 1


# --- 31. Жилая улица с палисадниками -----------------------------------------


def street_front_gardens() -> Primer:
    w, h = 120.0, 48.0
    p = Primer("31_street_front_gardens_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    y_side = street_south(p, w)
    for x0, x1 in ((6, 38), (44, 76), (82, 114)):
        house(p, x0, 21, x1, 33, 5, "S", 2, y_side)
        # Палисадник: низкий кизильник вдоль тротуара, цветущие кусты глубже.
        hedge(p, KIZILNIK, [(x0 - 2, y_side + 1.1), (x1 + 2, y_side + 1.1)], 1.1)
        hedge(p, SP_VANGUTTA, [(x0, y_side + 3.4), (x1, y_side + 3.4)], 2.2)
        hedge(p, GORTENZIYA, [(x0 + 1, 19.2), (x1 - 1, 19.2)], 1.8)
    for x in (41, 79):
        p.zone(f"PATH_PASSAGE_{x}", rect(x - 1.1, y_side, x + 1.1, h))
    p.zone("HEATING_T1", rect(0, 36.5, w, 38.0))  # ввод теплосети за домами
    # За домами -- регулярный боскет: два ряда строго друг против друга,
    # берёзы ближе к домам (4,5 м от теплосети -- норма 4 м), черёмуха у
    # границы; проходы во двор делят его на три части.
    for x in range(5, int(w), 6):
        p.plant(BEREZA, x, 42.5)
        p.plant(CHEREMUHA, x, 46.5)
    return p


# --- 32. Улица с магазинами на первых этажах ---------------------------------


def street_shops() -> Primer:
    w, h = 130.0, 58.0
    p = Primer("32_street_shops_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    y_side = street_south(p, w, sidewalk=6.0)  # широкий тротуар у витрин
    # Магазины -- входы с улицы, жилые подъезды -- со двора.
    house(p, 8, 21, 122, 35, 9, "S", 6, y_side, label="Жилой дом с магазинами, 9 эт.")
    p.zone("PATH_YARD", rect(6, 39, 124, 41.5))
    for f in (0.125, 0.375, 0.625, 0.875):
        x = 8 + 114 * f
        p.zone(f"PATH_ENTRANCE_Y{x:.0f}", rect(x - 1, 35, x + 1, 39))
        p.entrance(x, 35)
    hedge(p, SP_JAPAN, [(8, 19.1), (122, 19.1)], 1.2)
    for x0 in (6, 124):
        p.zone(f"PATH_PASSAGE_{x0}", rect(x0 - 3 if x0 > 60 else x0 - 1, y_side, x0 - 0.8 if x0 > 60 else x0 + 1.2, 41.5))
    # Двор: рощи из лип и клёнов (широкая крона -- 10 м от дома), сирень по краю.
    for cx in (22, 65, 108):
        p.plant(LIPA, cx, 50)
        for k, (dx, dy) in enumerate(((-4, 0), (4, 0), (0, 4), (0, -3.5))):
            p.plant(KLEN if k % 2 else LIPA, cx + dx, 50 + dy)
        hedge(p, SIREN, [(cx - 9, 45.5), (cx + 9, 45.5)], 2.5)
    for x in range(14, 124, 18):
        p.bench_with_urn(x, 42.3, 1, 0, 0)
    for x in range(22, 124, 26):
        p.put("lamp", x, 38.2)
    return p


# --- 33. Угловой квартал на перекрёстке --------------------------------------


def street_corner() -> Primer:
    s = 92.0
    p = Primer("33_street_corner_primer", rect(0, 0, s, s))
    p.lawn(rect(0, 0, s, s))
    p.zone("ROAD_S", rect(0, 0, s, 7))
    p.zone("ROAD_W", rect(0, 0, 7, s))
    p.zone("PATH_SIDEWALK_S", rect(11, 11, s, 14))
    p.zone("PATH_SIDEWALK_W", rect(11, 11, 14, s))
    p.zone("WATER_SUPPLY_S", rect(11, 11.8, s, 13.2))
    p.curb([(7, 7), (s, 7)])
    p.curb([(7, 7), (7, s)])
    for v in range(20, int(s), 8):
        p.plant(LIPA, v, 9.3)
        p.plant(LIPA, 9.3, v)
    # Г-образный дом из двух корпусов, подъезды -- во двор.
    house(p, 40, 24, 86, 36, 8, "N", 3, 40)
    house(p, 24, 40, 36, 86, 8, "E", 3, 40)
    p.zone("PATH_YARD_A", rect(40, 40, 88, 42))
    p.zone("PATH_YARD_B", rect(40, 40, 42, 88))
    # Угол перекрёстка -- сквер: диагональная дорожка к двору, цветущие
    # деревья и кусты по сторонам, скамейки вдоль неё.
    p.zone("PATH_DIAGONAL", strip([(13, 13), (41, 41)], 3.0))
    for x, y in ((20, 28), (28, 20), (18, 35), (35, 18), (26, 32), (32, 26)):
        p.plant(YABLONYA if (x + y) % 2 else CHEREMUHA, x, y)
    for x, y, tx, ty in along([(16, 16), (38, 38)], 7.0):
        p.bench_with_urn(x - ty * 2.4, y + tx * 2.4, tx, ty, 225)
    hedge(p, SP_VANGUTTA, [(15.5, 22), (15.5, 38)], 2.0)
    hedge(p, SP_VANGUTTA, [(22, 15.5), (38, 15.5)], 2.0)
    # Двор: липы и ели не ближе 10 и 5 м к корпусам.
    yard_trees(p, 48, 48, 90, 90, [LIPA, EL, KLEN], step=7.0)
    # Дорожки вокруг корпусов со стороны улиц и торцов (со двора их роль
    # играют дворовые дорожки) и изгородь из спиреи серой по их внешнему краю.
    p.zone("PATH_AROUND_A_S", rect(36, 21, 89, 23))
    p.zone("PATH_AROUND_A_E", rect(87, 21, 89, 40))
    p.zone("PATH_AROUND_A_W", rect(37, 21, 39, 39))
    p.zone("PATH_AROUND_B_W", rect(21, 36, 23, 89))
    p.zone("PATH_AROUND_B_N", rect(21, 87, 42, 89))
    p.zone("PATH_AROUND_B_S", rect(21, 37, 39, 39))
    for pts in ([(36, 20.2), (90, 20.2)], [(89.8, 20.2), (89.8, 39.5)], [(36.2, 20.2), (36.2, 35.8)],
                [(20.2, 35.8), (20.2, 90)], [(20.2, 89.8), (41.5, 89.8)], [(20.2, 36.2), (35.8, 36.2)]):
        hedge(p, SP_GRAY, pts, 1.1)
    facade_shrubs(p, SP_JAPAN, 40, 24, 86, 36, offset=2.2, sides="N")
    facade_shrubs(p, SP_JAPAN, 24, 40, 36, 86, offset=2.2, sides="E")
    for v in (54, 70, 86):
        p.put("lamp", v, 43)
        p.put("lamp", 43, v)
    return p


# --- 34. Дом вдоль улицы и двор за ним ---------------------------------------


def street_slab_yard() -> Primer:
    w, h = 100.0, 72.0
    p = Primer("34_street_slab_yard_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    y_side = street_south(p, w)
    house(p, 15, 20, 85, 32, 12, "N", 4, 36)
    hedge(p, KIZILNIK, [(15, y_side + 1.2), (85, y_side + 1.2)], 1.1, rows=(-0.45, 0.45))
    p.zone("PATH_YARD", rect(10, 36, 90, 38.5))
    p.zone("PATH_PASSAGE_W", rect(9, y_side, 11.5, 38.5))
    p.zone("PATH_PASSAGE_E", rect(88.5, y_side, 91, 38.5))
    p.zone("PLAYGROUND", rect(35, 48, 60, 62))
    p.zone("PATH_TO_PLAYGROUND", rect(46.5, 38.5, 48.5, 48))
    hedge_with_gaps(p, SP_JAPAN, (33.6, 46.6), (61.4, 46.6), 1.1, gaps=(14.0,), gap_half=2.2)
    hedge_with_gaps(p, SP_JAPAN, (33.6, 63.4), (61.4, 63.4), 1.1, gaps=(8.0, 20.0))
    for x in (41, 53):
        p.bench_with_urn(x, 63.4, 1, 0, 180)
    yard_trees(p, 6, 43, 96, 66, [BEREZA, KLEN, LIPA], step=6.5)
    hedge(p, KIZILNIK, [(2, 70.2), (98, 70.2)], 1.1)
    for x in (18, 34, 66, 82):
        p.put("lamp", x, 39.8)
    return p


# --- 35. Улица таунхаусов -----------------------------------------------------


def street_townhouses() -> Primer:
    w, h = 126.0, 50.0
    p = Primer("35_street_townhouses_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    p.zone("ROAD_STREET", rect(0, 0, w, 6))
    p.zone("PATH_SIDEWALK", rect(0, 6, w, 8.5))
    p.zone("WATER_SUPPLY", rect(0, 6.6, w, 7.9))
    p.curb([(0, 6), (w, 6)])
    for x in range(10, int(w), 10):
        p.plant(BEREZA, x, 10.5)
    for k in range(6):
        x0 = 5 + 20 * k
        house(p, x0, 20, x0 + 16, 30, 3, "S", 1, 8.5, label="Таунхаус, 3 эт.")
        # Свой палисадник у каждого дома: туи вдоль улицы, яблоня у входа.
        hedge_with_gaps(p, KIZILNIK, (x0, 12.9), (x0 + 16, 12.9), 1.1, gaps=(8.0,), gap_half=1.8)
        p.plant(YABLONYA, x0 + 3.5, 14.95)
        p.plant(TUYA, x0 + 12.5, 14.95)
        p.plant(SIREN, x0 - 2, 25)
        hedge(p, SP_BUMALDA, [(x0 + 1, 33), (x0 + 15, 33)], 1.4)
    # Общий сад за домами: яблони Недзведцкого по треугольной сетке --
    # ряды через 4,3 м со сдвигом на полшага, каждая яблоня в 5 м от шести
    # соседних.
    for j, y in enumerate((36.0, 40.33, 44.66)):
        for x in range(5, int(w) - 2, 5):
            p.plant(YABLONYA, x + (2.5 if j % 2 else 0.0), y)
    hedge(p, KIZILNIK, [(1.5, 48.3), (124.5, 48.3)], 1.1)
    for x in range(15, int(w), 20):
        p.put("lamp", x, 11.3)
    return p


# --- 36. Поликлиника со сквером у входа ---------------------------------------


def street_clinic() -> Primer:
    w, h = 110.0, 76.0
    p = Primer("36_street_clinic_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    y_side = street_south(p, w, tree=KLEN)
    p.building(rect(30, 40, 80, 58), 16.0, "Поликлиника, 4 эт.")
    p.entrance(55, 40)
    p.entrance(30, 49)
    p.zone("PATH_MAIN", rect(52, y_side, 58, 40))  # парадный подход
    p.zone("PATH_STAFF", rect(20, 48, 30, 50))
    p.zone("PATH_WEST", rect(18, y_side, 20, 62))
    # Регулярный сквер у входа: два ряда туй вдоль подхода, по бокам --
    # партеры из спиреи японской, скамейки лицом к подходу.
    for y in range(18, 36, 4):
        p.plant(TUYA, 45, y)
        p.plant(TUYA, 65, y)
    for x0, x1 in ((24, 42), (68, 86)):
        for y in (19.0, 21.4, 23.8, 26.2, 28.6):
            hedge(p, SP_JAPAN, [(x0, y), (x1, y)], 1.2)
    for y in (22, 30):
        p.bench_with_urn(50.8, y, 0, 1, 90)
        p.bench_with_urn(59.2, y, 0, 1, 270)
    for y in (18, 34):
        p.put("lamp", 50.9, y + 0.1)
        p.put("lamp", 59.1, y + 0.1)
    facade_shrubs(p, SP_GRAY, 30, 40, 80, 58, offset=2.4, step=2.2, sides="WE")
    # Края участка: симметричная дорожка справа, дорожка за зданием, по
    # обеим сторонам -- ряды клёнов и лип и сирень вдоль дорожек.
    p.zone("PATH_EAST", rect(90, y_side, 92, 62))
    p.zone("PATH_BACK", rect(18, 62, 92, 64))
    for x0, shrub_x in ((5.0, 15.6), (95.0, 94.4)):
        for j, y in enumerate(range(20, 58, 7)):
            p.plant(KLEN if j % 2 else LIPA, x0, y)
            p.plant(LIPA if j % 2 else KLEN, x0 + 6.0, y + 3.5)
        hedge(p, SIREN, [(shrub_x, 20), (shrub_x, 60)], 2.2)
    for x in range(12, 104, 9):
        p.plant(LIPA, x, 69)
    for y in (26, 42, 56):
        p.put("lamp", 17.2, y)
        p.put("lamp", 92.8, y)
    return p


# --- 37. Длинный дом вдоль проезда -------------------------------------------


def street_long_house() -> Primer:
    w, h = 170.0, 58.0
    p = Primer("37_street_long_house_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    p.zone("ROAD_STREET", rect(0, 0, w, 7))
    p.zone("PATH_SIDEWALK_STREET", rect(0, 7, w, 9.5))
    p.curb([(0, 7), (w, 7)])
    p.zone("ROAD_DRIVE", rect(6, 14, 164, 20))  # внутриквартальный проезд
    for i, x0 in enumerate(range(10, 160, 25)):
        p.zone(f"PARKING_{i + 1}", rect(x0, 20, x0 + 20, 25))
        p.plant(LIPA if i % 2 else KLEN, x0 + 22.5, 22.5)  # островок между карманами
    p.zone("PATH_FRONT", rect(4, 25.5, 166, 28))
    p.zone("HEATING_T1", rect(4, 26, 166, 27.3))  # теплосеть под тротуаром у дома
    house(p, 10, 32, 160, 45, 16, "S", 8, 28, label="Жилой дом, 16 эт.")
    facade_shrubs(p, SP_VANGUTTA, 10, 32, 160, 45, offset=2.2, step=2.4, sides="S")
    for x in range(8, int(w), 8):
        p.plant(LIPA, x, 11.8)
    for x in range(12, int(w), 24):
        p.put("lamp", x, 11.8)
    # За домом -- берёзы и ели, вдоль задней границы -- изгородь.
    for x in range(8, int(w), 9):
        p.plant(BEREZA if x % 18 else EL, x, 51.5)
    hedge(p, KIZILNIK, [(2, 56.3), (168, 56.3)], 1.1)
    return p


# --- 38. Улица с бульварной полосой посередине -------------------------------


def street_median() -> Primer:
    w, h = 140.0, 84.0
    p = Primer("38_street_median_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    p.zone("ROAD_S", rect(0, 33, w, 39))
    p.zone("ROAD_N", rect(0, 45, w, 51))
    p.zone("PATH_SIDEWALK_S", rect(0, 29, w, 33))
    p.zone("PATH_SIDEWALK_N", rect(0, 51, w, 55))
    p.zone("WATER_SUPPLY", rect(0, 30, w, 31.3))
    p.zone("CABLE_COMM", rect(0, 52.5, w, 53.3))
    for y in (33, 39, 45, 51):
        p.curb([(0, y), (w, y)])
    # Разделительная полоса -- липы по оси, кизильник низкой стрижкой по краям.
    for x in range(5, int(w), 8):
        p.plant(LIPA, x, 42)
    for x in range(9, int(w), 32):
        p.put("lamp", x, 42)
    # Между липами -- округлые куртины спиреи японской.
    for x in range(9, int(w), 8):
        for dx, dy in ((-1.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
            p.plant(SP_JAPAN, x + dx, 42 + dy)
    for x0, x1 in ((10, 65), (75, 130)):
        house(p, x0, 10, x1, 22, 9, "N", 3, 29)
        house(p, x0, 62, x1, 74, 9, "S", 3, 55)
        # Палисадники у домов по обе стороны улицы.
        hedge(p, KIZILNIK, [(x0, 27.9), (x1, 27.9)], 1.1)
        hedge(p, KIZILNIK, [(x0, 56.1), (x1, 56.1)], 1.1)
        hedge(p, GORTENZIYA, [(x0 + 1, 24.2), (x1 - 1, 24.2)], 2.0)
        hedge(p, GORTENZIYA, [(x0 + 1, 59.8), (x1 - 1, 59.8)], 2.0)
        for x in range(x0 + 4, x1, 8):
            p.plant(BEREZA, x, 4)
            p.plant(BEREZA, x, 80)
    return p


# --- 39. Улица с велодорожкой ------------------------------------------------


def street_bike_lane() -> Primer:
    w, h = 130.0, 50.0
    p = Primer("39_street_bike_lane_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    p.zone("ROAD_STREET", rect(0, 0, w, 7))
    p.zone("PATH_BIKE", rect(0, 10.2, w, 12.4))  # велодорожка
    p.zone("PATH_SIDEWALK", rect(0, 15.4, w, 18.4))
    p.zone("WATER_SUPPLY", rect(0, 16.2, w, 17.6))
    p.curb([(0, 7), (w, 7)])
    for x in range(4, int(w), 8):
        p.plant(LIPA, x, 9.3)
    # Между велодорожкой и тротуаром -- низкая спирея: разделяет потоки.
    hedge(p, SP_JAPAN, [(0, 13.9), (w, 13.9)], 1.2)
    for x0, x1 in ((8, 60), (70, 122)):
        entrances = house(p, x0, 26, x1, 38, 5, "S", 3, 18.4)
        hedge(p, KIZILNIK, [(x0 - 2, 19.5), (x1 + 2, 19.5)], 1.1)
        hedge(p, GORTENZIYA, [(x0, 23.2), (x1, 23.2)], 1.9)
        for ex, _ in entrances:
            p.put("bike_rack", ex + 2.6, 21.0)
        for x in range(x0 + 3, x1, 7):
            p.plant(BEREZA if x % 2 else DEREN, x, 44)
    for x in range(8, int(w), 16):
        p.put("lamp", x, 8.4)
    return p


# --- 40. Тупиковая улица с разворотной площадкой ------------------------------


def street_cul_de_sac() -> Primer:
    s = 96.0
    cx, cy = 48.0, 64.0
    p = Primer("40_street_cul_de_sac_primer", rect(0, 0, s, s))
    p.lawn(rect(0, 0, s, s))
    p.zone("ROAD_ACCESS", rect(44, 0, 52, 56))
    p.zone("ROAD_TURN", circle(cx, cy, 11, 64))
    p.zone("PATH_SIDE_W", rect(40, 0, 44, 55))
    p.zone("PATH_SIDE_E", rect(52, 0, 56, 55))
    for k, (a0, a1) in enumerate(((290, 360), (0, 90), (90, 180), (180, 250))):
        p.zone(f"PATH_RING_{k + 1}", arc_band(cx, cy, 11, 13.5, a0, a1))
    house(p, 8, 50, 26, 86, 5, "E", 2, 34.5)
    house(p, 70, 50, 88, 86, 5, "W", 2, 61.5)
    house(p, 34, 84, 62, 94, 5, "S", 2, 77.5)
    for x0, x1 in ((8, 26), (70, 88)):
        facade_shrubs(p, SP_VANGUTTA, x0, 50, x1, 86, offset=2.4, step=2.2, sides="EW")
    facade_shrubs(p, SP_VANGUTTA, 34, 84, 62, 94, offset=2.4, step=2.2, sides="S")
    # Южные участки -- рощи: берёзы, ели, лиственницы; у въезда -- аллея.
    for gx, gy in ((16, 18), (28, 34), (80, 18), (68, 34)):
        p.plant(EL, gx, gy)
        for i, (dx, dy) in enumerate(((-4, 0), (4, 0), (0, 4), (0, -4), (3, 3))):
            p.plant(BEREZA if i % 2 else LISTVENNICA, gx + dx, gy + dy)
        hedge(p, SIREN, [(gx - 8, gy - 8), (gx + 8, gy - 8)], 2.4)
    for y in range(6, 50, 8):
        p.plant(KLEN, 35.5, y)
        p.plant(KLEN, 60.5, y)
    for y in (10, 26, 42):
        p.put("lamp", 39.2, y)
        p.put("lamp", 56.8, y)
    p.put("lamp", 48, 79)
    for ang_x, ang_y, face in ((cx - 15, cy, 90), (cx + 15, cy, 270)):
        p.bench_with_urn(ang_x, ang_y, 0, 1, face)
    hedge(p, KIZILNIK, [(2, 2), (2, 94)], 1.1)
    hedge(p, KIZILNIK, [(94, 2), (94, 94)], 1.1)
    return p


STREET_BUILDERS = [street_front_gardens, street_shops, street_corner, street_slab_yard, street_townhouses,
                   street_clinic, street_long_house, street_median, street_bike_lane, street_cul_de_sac]
