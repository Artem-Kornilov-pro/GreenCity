"""
Сборка синтетических эталонных участков GreenPlan -- десяти образцов приёмов
озеленения для корпуса аналогов (backend/greenplan/pattern_corpus.py):

    ../../.venv/bin/python tools/primers/build_primers.py      (из корня репозитория)

Пишет locations/<slug>/<slug>.dxf. Описания (design_rationale.md) -- рядом,
написаны вручную; записи корпуса -- data/pattern_corpus.yaml. После пересборки
пересчитать признаки корпуса: make corpus-features.

Каждый участок -- законченный проект: дорожки, сети, МАФ и посадки с
названием вида на слое (парсер передаёт вид в сцену). Всё, что нарушило бы
нормативный отступ, отбрасывается при сборке (primer_kit.Primer.plant/put).
"""

from __future__ import annotations

import math
import sys

from primer_kit import Primer, along, arc_band, circle, rect, strip

LIPA = "Липа мелколистная"
KLEN = "Клен остролистный"
EL = "Ель колючая"
BEREZA = "Береза повислая"
DUB = "Дуб черешчатый"
TUYA = "Туя западная"
YABLONYA = "Яблоня Недзведцкого"
CHEREMUHA = "Черемуха Маака"
LISTVENNICA = "Лиственница европейская"
KIZILNIK = "Кизильник блестящий"
SP_VANGUTTA = "Спирея Вангутта"
SP_JAPAN = "Спирея японская"
SP_BUMALDA = "Спирея Бумальда"
SP_GRAY = "Спирея серая"
GORTENZIYA = "Гортензия метельчатая"
DEREN = "Дерен кроваво-красный"
SIREN = "Сирень обыкновенная"


def hedge(p: Primer, species: str, points, step: float, rows=(0.0,), stagger: bool = True) -> None:
    """Живая изгородь вдоль ломаной: ряды со смещением rows поперёк, в
    шахматном порядке, если рядов больше одного."""
    for i, offset in enumerate(rows):
        shift = step / 2 if stagger and i % 2 else 0.0
        for x, y, tx, ty in along(points, step, start=step / 2 + shift):
            p.plant(species, x - ty * offset, y + tx * offset)


def hedge_with_gaps(p: Primer, species: str, a, b, step: float, gaps=(), gap_half: float = 2.6, corner: float = 0.0) -> None:
    """Изгородь по отрезку a-b с разрывами: gaps -- расстояния от a до
    середины разрыва (место скамейки), corner -- отступ от концов отрезка
    (место фонаря или туи на углу)."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    tx, ty = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    d = corner + step / 2
    while d <= length - corner:
        if all(abs(d - g) > gap_half for g in gaps):
            p.plant(species, a[0] + tx * d, a[1] + ty * d)
        d += step


def ring_points(cx, cy, r, a0, a1, step_deg):
    a = a0
    while a <= a1 + 1e-9:
        yield cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
        a += step_deg


# --- 21. Шумозащитная полоса вдоль дороги ------------------------------------


def road_buffer() -> Primer:
    w, h = 150.0, 50.0
    p = Primer("21_road_buffer_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    p.zone("ROAD", rect(0, 20, w, 30))
    p.zone("WATER_SUPPLY", rect(0, 24.2, w, 25.8))  # водопровод под проезжей частью
    p.curb([(0, 20), (w, 20)])
    p.curb([(0, 30), (w, 30)])
    for sgn, base, name in ((1, 30.0, "N"), (-1, 20.0, "S")):
        def y(d, base=base, sgn=sgn):
            return base + sgn * d
        p.zone(f"PATH_SIDEWALK_{name}", rect(0, y(14), w, y(16.5)))
        p.zone(f"CABLE_COMM_{name}", rect(0, y(17.2), w, y(17.9)))
        p.curb([(0, y(14)), (w, y(14))])
        # 2 м газона от кромки -- затем низкие кусты в два ряда (пыль, брызги,
        # нижний ярус шумозащиты), за ними два ряда деревьев в шахматном
        # порядке: лиственный ряд и вечнозелёный -- защита и зимой.
        hedge(p, KIZILNIK, [(0, y(3.1)), (w, y(3.1))], 1.2, rows=(-0.5, 0.5))
        for x in range(4, int(w), 8):
            p.plant(LIPA, x, y(7))
            p.plant(EL, x + 4, y(11))
        for x in range(12, int(w), 24):
            p.put("lamp", x, y(13.2))
    return p


# --- 22. Круглая площадь с клумбой и лучами ----------------------------------


def circular_plaza() -> Primer:
    cx = cy = 40.0
    R = 36.0
    p = Primer("22_circular_plaza_primer", circle(cx, cy, R, 96))
    p.lawn(circle(cx, cy, R, 96))
    rays = [45.0, 135.0, 225.0, 315.0]
    # Кольцевая дорожка -- четырьмя дугами между лучами, лучи -- отдельно:
    # у каждого куска свой слой и нет дырок (см. primer_kit).
    for k, a in enumerate(rays):
        p.zone(f"PATH_RING_{k + 1}", arc_band(cx, cy, 5.5, 8.5, a, a + 90))
        ex, ey = cx + (R + 1) * math.cos(math.radians(a)), cy + (R + 1) * math.sin(math.radians(a))
        sx, sy = cx + 8.0 * math.cos(math.radians(a)), cy + 8.0 * math.sin(math.radians(a))
        p.zone(f"PATH_RAY_{k + 1}", strip([(sx, sy), (ex, ey)], 3.0).intersection(p.boundary))
    p.zone("WATER_SUPPLY", rect(0, 10.8, 80, 12.4).intersection(p.boundary))
    p.curb([(cx + 5.0 * math.cos(2 * math.pi * i / 48), cy + 5.0 * math.sin(2 * math.pi * i / 48)) for i in range(49)])

    # Клумба: три круга низких кустов -- в центре гортензия, затем спиреи.
    for r, n, species in ((1.2, 5, GORTENZIYA), (2.6, 10, SP_JAPAN), (4.0, 16, SP_BUMALDA)):
        for i in range(n):
            a = 2 * math.pi * i / n
            p.plant(species, cx + r * math.cos(a), cy + r * math.sin(a))
    for a in rays:
        mid = a + 45
        # Секторы: кольцо цветущих деревьев, кольцо кустов, кольцо крупных
        # деревьев, по краю -- низкая изгородь; у лучей места оставлены.
        for i, (x, y) in enumerate(ring_points(cx, cy, 15, mid - 27, mid + 27, 18)):
            p.plant(YABLONYA if i % 2 == 0 else CHEREMUHA, x, y)
        for x, y in ring_points(cx, cy, 20.5, mid - 35, mid + 35, 7):
            p.plant(SP_VANGUTTA, x, y)
        for i, (x, y) in enumerate(ring_points(cx, cy, 27, mid - 30, mid + 30, 12)):
            p.plant(LIPA if i % 2 == 0 else KLEN, x, y)
        for x, y in ring_points(cx, cy, 33.3, mid - 40, mid + 40, 2.3):
            p.plant(KIZILNIK, x, y)
        # Две скамейки с урнами у кольцевой дорожки, лицом к клумбе.
        for da in (-16, 16):
            ang = math.radians(mid + da)
            p.bench_with_urn(cx + 9.8 * math.cos(ang), cy + 9.8 * math.sin(ang), -math.sin(ang), math.cos(ang), math.degrees(ang) + 90)
    for a in rays:
        rad = math.radians(a)
        for r, side in ((12, 1), (21, -1), (30, 1)):
            nx, ny = -math.sin(rad) * side, math.cos(rad) * side
            p.put("lamp", cx + r * math.cos(rad) + nx * 2.3, cy + r * math.sin(rad) + ny * 2.3)
    return p


# --- 23. Двор с детской площадкой --------------------------------------------


def playground_yard() -> Primer:
    p = Primer("23_playground_yard_primer", rect(0, 0, 90, 70))
    p.lawn(rect(0, 0, 90, 70))
    p.building(rect(15, 54, 75, 66), 15.0, "Жилой дом, 5 этажей")
    p.building(rect(3, 12, 15, 50), 15.0, "Жилой дом, 5 этажей")
    p.building(rect(75, 12, 87, 50), 15.0, "Жилой дом, 5 этажей")
    entrances = [(27, 54), (45, 54), (63, 54), (15, 22), (15, 40), (75, 22), (75, 40)]
    for x, y in entrances:
        p.entrance(x, y)
    p.zone("PLAYGROUND", rect(33, 18, 57, 34))
    # Кольцо дорожки вокруг площадки -- четыре стороны, отдельные слои.
    p.zone("PATH_RING_S", rect(28.5, 13, 61.5, 15.5))
    p.zone("PATH_RING_N", rect(28.5, 36.5, 61.5, 39))
    p.zone("PATH_RING_W", rect(28.5, 13, 31, 39))
    p.zone("PATH_RING_E", rect(59, 13, 61.5, 39))
    # Дорожки вдоль домов с подходами к каждому подъезду и связи с кольцом.
    p.zone("PATH_FRONT_N", rect(19, 45, 71, 47.5))
    p.zone("PATH_FRONT_W", rect(19, 16, 21.5, 47.5))
    p.zone("PATH_FRONT_E", rect(68.5, 16, 71, 47.5))
    for i, (x, y) in enumerate(entrances):
        if y == 54:
            p.zone(f"PATH_ENTRANCE_{i}", rect(x - 1, 47.5, x + 1, 54))
        elif x == 15:
            p.zone(f"PATH_ENTRANCE_{i}", rect(15, y - 1, 19, y + 1))
        else:
            p.zone(f"PATH_ENTRANCE_{i}", rect(71, y - 1, 75, y + 1))
    p.zone("PATH_LINK_N", rect(44, 39, 46.5, 45))
    p.zone("PATH_LINK_W", rect(21.5, 25, 28.5, 27.5))
    p.zone("PATH_LINK_E", rect(61.5, 25, 68.5, 27.5))
    p.zone("PATH_EXIT", rect(44, 0, 46.5, 13))
    p.zone("HEATING_T1", rect(22, 0, 23.6, 45))  # теплосеть под западной дорожкой
    p.zone("WATER_SUPPLY", rect(19, 49, 71, 50.2))

    # Скамейки для родителей -- по краю площадки, лицом к ней; защитная
    # изгородь (неколючая, неядовитая спирея) -- в 1,4 м от площадки, с
    # разрывами под скамейки.
    for x in (38, 52):
        p.bench_with_urn(x, 16.6, 1, 0, 0)
        p.bench_with_urn(x, 35.4, 1, 0, 180)
    for y in (22, 30):
        p.bench_with_urn(31.6, y, 0, 1, 270)
        p.bench_with_urn(58.4, y, 0, 1, 90)
    hedge_with_gaps(p, SP_JAPAN, (31.6, 16.6), (58.4, 16.6), 1.1, gaps=(6.4, 20.4))
    hedge_with_gaps(p, SP_JAPAN, (31.6, 35.4), (58.4, 35.4), 1.1, gaps=(6.4, 20.4))
    hedge_with_gaps(p, SP_JAPAN, (31.6, 16.6), (31.6, 35.4), 1.1, gaps=(5.4, 13.4), corner=1.2)
    hedge_with_gaps(p, SP_JAPAN, (58.4, 16.6), (58.4, 35.4), 1.1, gaps=(5.4, 13.4), corner=1.2)
    # Фонари вдоль дорожек -- в метре от края, через ~14 м.
    for pts, dx, dy in (([(19, 48.4), (71, 48.4)], 0, 0), ([(18.1, 16), (18.1, 45)], 0, 0), ([(71.9, 16), (71.9, 45)], 0, 0),
                        ([(28.5, 12.1), (61.5, 12.1)], 0, 0), ([(47.4, 1), (47.4, 12)], 0, 0)):
        for x, y, _, _ in along(pts, 14.0):
            p.put("lamp", x + dx, y + dy)
    # Тень: деревья по свободной площади двора (берёза -- 5 м от дома, клён и
    # липа с широкой кроной -- 10 м); всё, что ближе нормы, отсеивается.
    for i, x in enumerate(range(5, 86, 5)):
        for j, y in enumerate(range(4, 52, 5)):
            p.plant((BEREZA, KLEN, LIPA)[(i + j) % 3], x + (j % 2) * 2.5, y)
    # Кусты вдоль фасадов (в полосе у дома), у подъездов -- разрыв.
    hedge(p, SP_VANGUTTA, [(16, 51.5), (74, 51.5)], 2.2)
    hedge(p, SP_VANGUTTA, [(17.5, 13), (17.5, 44)], 2.2)
    hedge(p, SP_VANGUTTA, [(72.5, 13), (72.5, 44)], 2.2)
    hedge(p, KIZILNIK, [(1.5, 1.8), (88.5, 1.8)], 1.1, rows=(-0.5, 0.5))
    return p


# --- 24. Бульвар: аллея между проездами --------------------------------------


def boulevard() -> Primer:
    w = 160.0
    p = Primer("24_boulevard_primer", rect(0, 0, w, 40))
    p.lawn(rect(0, 0, w, 40))
    p.zone("ROAD_S", rect(0, 0, w, 7))
    p.zone("ROAD_N", rect(0, 33, w, 40))
    p.zone("PATH_SIDEWALK_S", rect(0, 7, w, 9.5))
    p.zone("PATH_SIDEWALK_N", rect(0, 30.5, w, 33))
    p.zone("PATH_PROMENADE", rect(0, 18, w, 22))
    for i, x in enumerate((49, 109)):
        p.zone(f"PATH_CROSS_S{i}", rect(x, 9.5, x + 2.5, 18))
        p.zone(f"PATH_CROSS_N{i}", rect(x, 22, x + 2.5, 30.5))
    p.zone("WATER_SUPPLY", rect(0, 7.6, w, 8.9))  # под южным тротуаром
    p.zone("CABLE_COMM", rect(0, 19.6, w, 20.4))  # под прогулочной дорожкой
    for y in (7, 33):
        p.curb([(0, y), (w, y)])
    # Изгородь от проезжих частей (пыль, шум), двойная аллея лип вдоль
    # прогулочной дорожки -- классический московский бульвар.
    hedge(p, KIZILNIK, [(0, 11.4), (w, 11.4)], 1.1, rows=(-0.45, 0.45))
    hedge(p, KIZILNIK, [(0, 28.6), (w, 28.6)], 1.1, rows=(-0.45, 0.45))
    for x in range(6, int(w), 8):
        p.plant(LIPA, x, 15)
        p.plant(LIPA, x, 25)
    for x in range(10, int(w), 16):
        p.bench_with_urn(x, 16.8, 1, 0, 0)
        p.bench_with_urn(x + 8, 23.2, 1, 0, 180)
    # Фонари -- в 4 м от лип (x = 2 mod 8), чередуясь по сторонам и не
    # занимая мест скамеек.
    for x in range(2, int(w), 32):
        p.put("lamp", x, 17.3)
    for x in range(26, int(w), 32):
        p.put("lamp", x, 22.7)
    return p


# --- 25. Дом в строгом обрамлении: периметр, подъезды, дорожки ---------------


def classical_building() -> Primer:
    p = Primer("25_classical_building_ring_primer", rect(0, 0, 90, 60))
    p.lawn(rect(0, 0, 90, 60))
    p.building(rect(27, 30, 63, 44), 27.0, "Жилой дом, 9 этажей")
    entrances = [(33, 30), (45, 30), (57, 30)]
    for x, y in entrances:
        p.entrance(x, y)
    p.zone("PATH_MAIN", rect(6, 14, 84, 16.5))
    p.zone("PATH_GATE", rect(43.5, 0, 46.5, 14))
    for i, (x, _) in enumerate(entrances):
        p.zone(f"PATH_ENTRANCE_{i + 1}", rect(x - 1, 16.5, x + 1, 30))
    p.zone("WATER_SUPPLY", rect(38.6, 0, 40.0, 30))  # ввод водопровода
    p.zone("SEWER", rect(50.0, 0, 51.4, 30))  # выпуск канализации
    for pts in ([(6, 14), (84, 14)], [(6, 16.5), (84, 16.5)]):
        p.curb(pts)

    # Периметр: стриженая двухрядная изгородь по границе участка и липы за ней.
    perimeter = [(2.3, 2.3), (87.7, 2.3), (87.7, 57.7), (2.3, 57.7), (2.3, 2.3)]
    hedge(p, KIZILNIK, perimeter, 1.0, rows=(-0.45, 0.45))
    for x, y, _, _ in along([(6.5, 6.5), (83.5, 6.5), (83.5, 53.5), (6.5, 53.5), (6.5, 6.5)], 9.0, start=0.0):
        p.plant(LIPA, x, y)
    # Строгое кольцо туй вокруг дома и низкий бордюр спиреи у фасада.
    for x, y, _, _ in along([(21, 24), (69, 24), (69, 50), (21, 50), (21, 24)], 3.0, start=0.0):
        p.plant(TUYA, x, y)
    hedge(p, SP_JAPAN, [(25, 28), (65, 28), (65, 46), (25, 46), (25, 28)], 1.2)
    for x in range(10, 84, 15):
        p.put("lamp", x, 17.8)
    for x in range(17, 84, 15):
        p.bench_with_urn(x, 12.7, 1, 0, 0)
    return p


# --- 26. Сад полос: одна полоса -- один вид ----------------------------------


def stripe_garden() -> Primer:
    size = 64.0
    p = Primer("26_diagonal_garden_primer", rect(0, 0, size, size))
    p.lawn(rect(0, 0, size, size))
    c = size / 2
    u = (math.sqrt(0.5), math.sqrt(0.5))  # вдоль полос
    n = (-math.sqrt(0.5), math.sqrt(0.5))  # поперёк
    half = size * 0.75

    def line_at(offset):
        ox, oy = c + n[0] * offset, c + n[1] * offset
        return [(ox - u[0] * half, oy - u[1] * half), (ox + u[0] * half, oy + u[1] * half)]

    for i, offset in enumerate((-24.5, -3.5, 17.5)):
        p.zone(f"PATH_DIAGONAL_{i + 1}", strip(line_at(offset), 2.2).intersection(p.boundary))
    p.zone("GAS_PIPE", rect(58, 0, 59.6, size))
    # Три вида кустарника, каждая полоса -- один вид: белая арочная спирея,
    # метельчатая гортензия, розовая спирея -- повторяются по кругу.
    species = [SP_VANGUTTA, GORTENZIYA, SP_JAPAN]
    for k in range(-6, 7):
        hedge(p, species[k % 3], line_at(7.0 * k), 1.3, rows=(-0.6, 0.6))
    for offset in (-24.5, -3.5, 17.5):
        pts = line_at(offset)
        for j, (x, y, tx, ty) in enumerate(along(pts, 16.0)):
            p.put("lamp", x - ty * 1.6 * (1 if j % 2 else -1), y + tx * 1.6 * (1 if j % 2 else -1))
        for x, y, tx, ty in along(pts, 20.0, start=10.0):
            p.bench_with_urn(x + ty * 1.7, y - tx * 1.7, tx, ty, math.degrees(math.atan2(ty, tx)))
    return p


# --- 27. Волны: волновые посадки и дорожки по волне -------------------------


def flowing_meadow() -> Primer:
    w, h = 110.0, 60.0
    p = Primer("27_flowing_meadow_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))
    amp, length = 3.5, 36.0

    def wave(y0, dx=0.0, x0=0.0, x1=w):
        pts = []
        x = x0
        while x <= x1 + 1e-9:
            pts.append((x, y0 + amp * math.sin(2 * math.pi * (x + dx) / length)))
            x += 1.0
        return pts

    # Две дорожки идут по волне между полосами посадок, одна пересекает их.
    p.zone("PATH_WAVE_S", strip(wave(15.0), 2.4))
    p.zone("PATH_WAVE_N", strip(wave(45.0), 2.4))
    p.zone("PATH_CROSS", strip([(55, 0), (55, h)], 2.4))
    p.zone("SEWER", rect(88, 0, 89.4, h))
    bands = [(5.5, DEREN), (24.5, SP_VANGUTTA), (35.5, GORTENZIYA), (54.5, SP_JAPAN)]
    for y0, species in bands:
        hedge(p, species, wave(y0), 1.3, rows=(-0.6, 0.6))
    # Берёзовые рощицы и ели -- на гребнях волн, между полосами кустов.
    for x0 in (9, 45, 81):
        for dx, dy in ((0, 0), (3.2, 1.5), (-1.5, 3.0)):
            x = x0 + dx
            p.plant(BEREZA, x, 30 + amp * math.sin(2 * math.pi * x / length) + dy)
    for x0 in (27, 63, 99):
        x = x0
        p.plant(EL, x, 30 + amp * math.sin(2 * math.pi * x / length))
    for y0 in (15.0, 45.0):
        for j, (x, y, tx, ty) in enumerate(along(wave(y0), 18.0)):
            side = 1 if j % 2 else -1
            p.put("lamp", x - ty * 1.9 * side, y + tx * 1.9 * side)
        for x, y, tx, ty in along(wave(y0), 27.0, start=9.0):
            p.bench_with_urn(x + ty * 1.9, y - tx * 1.9, tx, ty, math.degrees(math.atan2(ty, tx)))
    return p


# --- 28. Регулярный сад квадратов с дубами -----------------------------------


def oak_squares() -> Primer:
    size = 72.0
    p = Primer("28_formal_bosque_primer", rect(0, 0, size, size))
    p.lawn(rect(0, 0, size, size))
    lines = (22.5, 46.5)  # дорожки 3 м делят квадрат на 3 x 3
    for i, a in enumerate(lines):
        p.zone(f"PATH_V{i + 1}", rect(a, 0, a + 3, size))
        p.zone(f"PATH_H{i + 1}", rect(0, a, size, a + 3))
    p.zone("CABLE_COMM", rect(23.6, 0, 24.4, size))  # кабель связи под дорожкой
    spans = [(0.0, 22.5), (25.5, 46.5), (49.5, size)]
    for x0, x1 in spans:
        for y0, y1 in spans:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            p.plant(DUB, cx, cy)  # в центре каждого квадрата -- дуб
            for i in range(14):
                a = 2 * math.pi * i / 14
                p.plant(SP_GRAY, cx + 6.5 * math.cos(a), cy + 6.5 * math.sin(a))
            # Бордюр из стриженого кизильника по краю квадрата; в середине
            # сторон, выходящих на дорожку, -- разрыв и скамейка лицом к дубу,
            # на углах -- туи, у перекрёстков -- место фонарю.
            inset = 1.6
            corners = [(x0 + inset, y0 + inset), (x1 - inset, y0 + inset), (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)]
            for k in range(4):
                a, b = corners[k], corners[(k + 1) % 4]
                side_mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                on_path = any(abs(side_mid[0] - (v - inset)) < 1e-6 or abs(side_mid[0] - (v + 3 + inset)) < 1e-6 or
                              abs(side_mid[1] - (v - inset)) < 1e-6 or abs(side_mid[1] - (v + 3 + inset)) < 1e-6 for v in lines)
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                hedge_with_gaps(p, KIZILNIK, a, b, 1.0, gaps=(length / 2,) if on_path else (), corner=2.6)
                if on_path:
                    nx, ny = (cx - side_mid[0]), (cy - side_mid[1])
                    norm = math.hypot(nx, ny)
                    bx, by = side_mid[0] - nx / norm * 0.7, side_mid[1] - ny / norm * 0.7
                    p.bench_with_urn(bx, by, (b[0] - a[0]) / length, (b[1] - a[1]) / length, math.degrees(math.atan2(ny, nx)))
            for x, y in ((x0 + 3.6, y0 + 3.6), (x1 - 3.6, y0 + 3.6), (x1 - 3.6, y1 - 3.6), (x0 + 3.6, y1 - 3.6)):
                p.plant(TUYA, x, y)
    for a in lines:
        for b in lines:
            p.put("lamp", a - 0.6, b - 0.6)
    return p


# --- 29. Озеленённая парковка у дома ------------------------------------------


def green_parking() -> Primer:
    p = Primer("29_green_parking_primer", rect(0, 0, 80, 56))
    p.lawn(rect(0, 0, 80, 56))
    p.building(rect(10, 40, 70, 52), 27.0, "Жилой дом, 9 этажей")
    entrances = [(22, 40), (40, 40), (58, 40)]
    for x, y in entrances:
        p.entrance(x, y)
    p.zone("ROAD_STREET", rect(0, 0, 80, 4))
    p.zone("ROAD_ENTRY", rect(36, 4, 44, 8))
    p.zone("PATH_FRONT", rect(8, 33, 72, 35.5))
    for i, (x, _) in enumerate(entrances):
        p.zone(f"PATH_ENTRANCE_{i + 1}", rect(x - 1, 35.5, x + 1, 40))
    # Ряды машиномест кусками по 4 места (10 м), между ними -- зелёные
    # островки с деревом; проезд -- отдельно.
    blocks = [(10, 20), (22.5, 32.5), (35, 45), (47.5, 57.5), (60, 70)]
    for i, (x0, x1) in enumerate(blocks):
        p.zone(f"PARKING_S{i + 1}", rect(x0, 8, x1, 13))
        p.zone(f"PARKING_N{i + 1}", rect(x0, 19, x1, 24))
    p.zone("PARKING_AISLE", rect(8, 13, 72, 19))
    p.zone("GAS_PIPE", rect(29.2, 24, 30.6, 40))  # ввод газа в дом
    for i, (_, x1) in enumerate(blocks[:-1]):
        cx = x1 + 1.25
        p.plant(KLEN if i % 2 else LIPA, cx, 10.5)
        p.plant(LIPA if i % 2 else KLEN, cx, 21.5)
    # Зелёный экран между машинами и окнами: двойной ряд кустов и берёзы.
    hedge(p, SP_VANGUTTA, [(6, 27.6), (74, 27.6)], 1.3, rows=(-0.6, 0.6))
    for x in range(12, 72, 9):
        p.plant(BEREZA, x, 30.6)
    hedge(p, SP_JAPAN, [(11, 38.3), (69, 38.3)], 1.2)
    hedge(p, KIZILNIK, [(2.5, 6), (2.5, 50)], 1.1, rows=(-0.45, 0.45))
    hedge(p, KIZILNIK, [(77.5, 6), (77.5, 50)], 1.1, rows=(-0.45, 0.45))
    for x in (16, 34, 52, 68):
        p.put("lamp", x, 25.4)
    for x in (14, 31, 49, 66):
        p.put("lamp", x, 36.8)
    p.put("bike_rack", 46.5, 37.2)
    return p


# --- 30. Сквер над инженерными сетями ----------------------------------------


def network_corridor_garden() -> Primer:
    w, h = 90.0, 60.0
    p = Primer("30_network_corridor_garden_primer", rect(0, 0, w, h))
    p.lawn(rect(0, 0, w, h))

    def line(y0):
        return [(0, y0), (w, y0 + 12)]

    # Коридор сетей наискосок через сквер: теплосеть и водопровод. Над ними --
    # только газон и прогулочная дорожка (дорожке сети не мешают), деревья --
    # за нормативным отступом, рощами по обе стороны.
    p.zone("HEATING_T1", strip(line(24.0), 3.0).intersection(p.boundary))
    p.zone("WATER_SUPPLY", strip(line(29.5), 1.6).intersection(p.boundary))
    p.zone("PATH_ALONG", strip(line(26.5), 2.5).intersection(p.boundary))
    p.zone("PATH_TO_SOUTH", strip([(30, 0), (30, 29.5)], 2.2))
    p.zone("PATH_TO_NORTH", strip([(62, 34.9), (62, h)], 2.2))
    groves = [((14, 9), (BEREZA, LISTVENNICA)), ((47, 7), (BEREZA, EL)), ((75, 13), (LISTVENNICA, BEREZA)),
              ((13, 48), (EL, BEREZA)), ((40, 52), (BEREZA, LISTVENNICA)), ((78, 51), (BEREZA, EL))]
    for (gx, gy), (main, accent) in groves:
        p.plant(accent, gx, gy)
        for i in range(5):
            a = 2 * math.pi * i / 5 + 0.4
            p.plant(main, gx + 4.2 * math.cos(a), gy + 4.2 * math.sin(a))
        for i in range(9):
            a = 2 * math.pi * i / 9
            p.plant(SIREN if i % 2 else SP_VANGUTTA, gx + 8.0 * math.cos(a), gy + 8.0 * math.sin(a))
    for x, y, tx, ty in along(line(26.5), 15.0):
        p.put("lamp", x + ty * 5.0, y - tx * 5.0)
    for x, y, tx, ty in along(line(26.5), 22.0, start=11.0):
        p.bench_with_urn(x - ty * 4.4, y + tx * 4.4, tx, ty, math.degrees(math.atan2(ty, tx)) + 180)
    return p


BUILDERS = [road_buffer, circular_plaza, playground_yard, boulevard, classical_building,
            stripe_garden, flowing_meadow, oak_squares, green_parking, network_corridor_garden]


if __name__ == "__main__":
    for build in BUILDERS:
        primer = build()
        path = primer.write()
        kinds = {}
        for _, kind, _, _ in primer.plants:
            kinds[kind] = kinds.get(kind, 0) + 1
        furniture = {}
        for ftype, *_ in primer.furniture:
            furniture[ftype] = furniture.get(ftype, 0) + 1
        print(f"{primer.slug}: {kinds}, {furniture}, отброшено по нормам {primer.rejected} -> {path.relative_to(path.parents[2])}")
        if "-v" in sys.argv:
            print("   ", dict(primer.reasons.most_common(8)))
