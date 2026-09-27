"""
Случаи оценки ИИ-ассистента (run.py): просьба (или цепочка просьб -- для
отсылок к прошлым правкам) и проверка результата. check получает список
Outcome (по одному на просьбу) и возвращает список проблем -- пустой, если
всё хорошо.

Участки: yard -- пустой двор из трёх домов с фонарями и сетями
(location_old/02); play -- двор с детской площадкой, подъездами, скамейками,
урнами и посадками конкретных видов (эталон 23); real -- большой реальный
проект (12_natashinsky_proezd, больше 600 объектов).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from shapely.geometry import Point


@dataclass
class Case:
    id: str
    scene: str
    turns: list[str]
    check: Callable[[list], list[str]]
    setup: Optional[Callable] = None


def _dist(obj, x, z):
    return math.hypot(obj.position.x - x, obj.position.z - z)


def _species(objs) -> set[str]:
    return {str(o.metadata.get("species", "")) for o in objs}


def expect(*conditions: tuple[bool, str]) -> list[str]:
    return [message for ok, message in conditions if not ok]


def no_violations(o) -> tuple[bool, str]:
    return (not o.violations, f"нарушений норм у новых/изменённых объектов: {len(o.violations)}")


def _selection_setup(scene):
    """Квадрат 16x16 м в середине самой большой свободной области."""
    from run import with_selection

    from core.placement import Placer

    area = max(Placer(scene).free_areas(), key=lambda p: p.area)
    c = area.representative_point()
    x, z = c.x, c.y
    return with_selection(scene, [(x - 8, z - 8), (x + 8, z - 8), (x + 8, z + 8), (x - 8, z + 8)])


def _selection_check(outs):
    o = outs[0]
    sel = next(z for z in o.after.restrictions if z.type == "selection")
    from shapely.geometry import Polygon

    poly = Polygon([(p.x, p.z) for p in sel.polygon]).buffer(0.5)
    bushes = o.added_of("bush")
    outside = [b for b in bushes if not poly.contains(Point(b.position.x, b.position.z))]
    return expect(
        (len(bushes) >= 3, f"кустов в выделении: {len(bushes)}, ждали >= 3"),
        (not outside, f"кусты вне выделения: {len(outside)}"),
        no_violations(o),
    )


def _playground_near(o, objs, max_d):
    pg = o.zone("playground_zone")
    return [b for b in objs if pg.exterior.distance(Point(b.position.x, b.position.z)) > max_d]


def _min_spacing(objs) -> float:
    pts = [(o.position.x, o.position.z) for o in objs]
    best = math.inf
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            best = min(best, math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
    return best


def _first_entrance(o):
    return min(o.entrances(), key=lambda e: (e.position.x, e.position.z))


CASES: list[Case] = [
    # --- Пустой двор ------------------------------------------------------
    Case("yard_lindens_along_paths", "yard", ["посади 5 лип вдоль дорожек"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 5, f"деревьев добавлено {len(outs[0].added_of('tree'))}, ждали 5"),
        (all(s.startswith("Липа") for s in _species(outs[0].added_of("tree"))), f"виды: {_species(outs[0].added_of('tree'))}"),
        no_violations(outs[0]),
    )),
    Case("yard_lilac_facades", "yard", ["посади кусты сирени вдоль фасадов домов"], lambda outs: expect(
        (len(outs[0].added_of("bush")) >= 8, f"кустов {len(outs[0].added_of('bush'))}, ждали >= 8"),
        (all(s.startswith("Сирень") for s in _species(outs[0].added_of("bush"))), f"виды: {_species(outs[0].added_of('bush'))}"),
        no_violations(outs[0]),
    )),
    Case("yard_remove_lamps", "yard", ["убери все фонари"], lambda outs: expect(
        (outs[0].count(outs[0].after, "lamp") == 0, f"фонарей осталось {outs[0].count(outs[0].after, 'lamp')}"),
    )),
    Case("yard_greenplan_landscape", "yard", ["озелени участок в пейзажном стиле, без газона, но с дорожками и скамейками"], lambda outs: expect(
        (outs[0].result.greenplan is not None, "GreenPlan не запрошен"),
        (outs[0].result.greenplan is not None and outs[0].result.greenplan.style == "landscape", "стиль не пейзажный"),
        (outs[0].result.greenplan is not None and not outs[0].result.greenplan.lawn, "газон не выключен"),
        (outs[0].result.greenplan is not None and outs[0].result.greenplan.paths and outs[0].result.greenplan.benches, "нет дорожек/скамеек"),
    )),
    Case("yard_lawn_everywhere", "yard", ["засей газоном всю свободную площадь"], lambda outs: expect(
        (len(outs[0].added_of("lawn_patch")) >= 20, f"участков газона {len(outs[0].added_of('lawn_patch'))}, ждали >= 20"),
    )),
    Case("yard_exactly_12_trees", "yard", ["посади ровно 12 деревьев равномерно по двору"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 12, f"деревьев {len(outs[0].added_of('tree'))}, ждали 12"),
        no_violations(outs[0]),
    )),
    Case("yard_spruces_perimeter", "yard", ["посади 3 ели по периметру участка"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 3, f"деревьев {len(outs[0].added_of('tree'))}, ждали 3"),
        (all(s.startswith("Ель") for s in _species(outs[0].added_of("tree"))), f"виды: {_species(outs[0].added_of('tree'))}"),
        no_violations(outs[0]),
    )),
    Case("yard_baobab", "yard", ["посади баобаб в центре двора"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 1, f"деревьев {len(outs[0].added_of('tree'))}, ждали 1 похожее"),
        ("баобаб" in outs[0].result.explanation.lower(), f"в ответе не объяснено про баобаб: {outs[0].result.explanation!r}"),
    )),
    Case("yard_what_can_you_do", "yard", ["что ты умеешь?"], lambda outs: expect(
        (not outs[0].added and not outs[0].removed, "вопрос изменил план"),
        (len(outs[0].result.explanation) >= 60, f"ответ слишком короткий: {outs[0].result.explanation!r}"),
    )),
    Case("yard_fountain_design", "yard", ["сделай благоустройство двора с фонтаном"], lambda outs: expect(
        (len(outs[0].added_of("fountain")) == 1, f"фонтанов {len(outs[0].added_of('fountain'))}"),
        (len(outs[0].added_of("path_segment")) > 0, "нет дорожек"),
    )),
    # --- Двор с детской площадкой -----------------------------------------
    Case("play_hedge_around_playground", "play", ["огороди детскую площадку живой изгородью"], lambda outs: expect(
        (len(outs[0].added) >= 6, f"добавлено {len(outs[0].added)}, ждали изгородь >= 6 секций"),
        # До 8 м: вокруг площадки в 2 м идут дорожки -- ограда встаёт за ними.
        (not _playground_near(outs[0], outs[0].added, 8.0), "часть изгороди дальше 8 м от площадки"),
        no_violations(outs[0]),
    )),
    Case("play_two_benches", "play", ["поставь две скамейки у детской площадки"], lambda outs: expect(
        (len(outs[0].added_of("bench")) == 2, f"скамеек {len(outs[0].added_of('bench'))}, ждали 2"),
        (not _playground_near(outs[0], outs[0].added_of("bench"), 12.0), "скамейка дальше 12 м от площадки"),
    )),
    Case("play_maples_to_lindens", "play", ["замени все клёны на липы"], lambda outs: expect(
        (outs[0].count(outs[0].before, "tree", "Клен") > 0, "в сцене нет клёнов -- случай бессмыслен"),
        (outs[0].count(outs[0].after, "tree", "Клен") == 0, f"клёнов осталось {outs[0].count(outs[0].after, 'tree', 'Клен')}"),
        (outs[0].count(outs[0].after, "tree", "Липа") == outs[0].count(outs[0].before, "tree", "Липа") + outs[0].count(outs[0].before, "tree", "Клен"),
         "лип стало не на число клёнов больше"),
    )),
    Case("play_thin_bushes", "play", ["проредь кустарники, чтобы между ними было не меньше 3 метров"], lambda outs: expect(
        (len(outs[0].removed) > 0, "ничего не убрано"),
        (_min_spacing([o for o in outs[0].after.objects if o.type == "bush"]) >= 2.95,
         f"мин. расстояние между кустами {_min_spacing([o for o in outs[0].after.objects if o.type == 'bush']):.2f} м"),
    )),
    Case("play_remove_urns", "play", ["удали все урны"], lambda outs: expect(
        (outs[0].count(outs[0].after, "urn") + outs[0].count(outs[0].after, "trash") == 0,
         f"урн осталось {outs[0].count(outs[0].after, 'urn') + outs[0].count(outs[0].after, 'trash')}"),
    )),
    Case("play_benches_face_playground", "play", ["разверни все скамейки к детской площадке"], lambda outs: expect(
        (len([o for o in outs[0].changed if o.type == "bench"]) >= 4, f"развёрнуто скамеек: {len([o for o in outs[0].changed if o.type == 'bench'])}"),
    )),
    Case("play_path_entrance_playground", "play", ["проложи дорожку от первого подъезда до детской площадки"], lambda outs: expect(
        (len(outs[0].added_of("path_segment")) > 0, "дорожка не проложена"),
    )),
    Case("play_birches_then_remove", "play", ["посади 4 берёзы у первого подъезда", "а теперь убери их"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 4, f"на первом шаге деревьев {len(outs[0].added_of('tree'))}, ждали 4"),
        (all(s.startswith("Береза") for s in _species(outs[0].added_of("tree"))), f"виды: {_species(outs[0].added_of('tree'))}"),
        ({o.id for o in outs[0].added} <= {o.id for o in outs[1].removed}, "не все посаженные берёзы убраны"),
        (len(outs[1].removed) == len(outs[0].added), f"убрано {len(outs[1].removed)}, а посажено {len(outs[0].added)} -- задето лишнее"),
    )),
    Case("play_thuja_then_more", "play", ["посади 3 туи у второго подъезда", "добавь ещё столько же"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 3, f"на первом шаге {len(outs[0].added_of('tree'))}, ждали 3"),
        (len(outs[1].added_of("tree")) == 3, f"на втором шаге {len(outs[1].added_of('tree'))}, ждали 3"),
        (all(s.startswith("Туя") for s in _species(outs[1].added_of("tree"))), f"виды второго шага: {_species(outs[1].added_of('tree'))}"),
    )),
    Case("play_bushes_in_selection", "play", ["посади здесь кусты"], _selection_check, setup=_selection_setup),
    Case("play_trees_by_entrance_bigger", "play", ["сделай деревья у первого подъезда покрупнее"], lambda outs: expect(
        (len([o for o in outs[0].changed if o.type == "tree" and o.scale > 1.0]) >= 1, "ни одно дерево не увеличено"),
    )),
    Case("play_remove_japanese_spirea", "play", ["убери всю спирею японскую"], lambda outs: expect(
        (outs[0].count(outs[0].after, "bush", "Спирея японская") == 0, f"спиреи японской осталось {outs[0].count(outs[0].after, 'bush', 'Спирея японская')}"),
        (outs[0].count(outs[0].after, "bush", "Спирея Вангутта") == outs[0].count(outs[0].before, "bush", "Спирея Вангутта"), "задета спирея Вангутта"),
    )),
    Case("play_plant_then_smaller", "play", ["посади 5 лип вдоль дорожек", "сделай их поменьше"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 5, f"на первом шаге {len(outs[0].added_of('tree'))}, ждали 5"),
        ({o.id for o in outs[1].changed} == {o.id for o in outs[0].added}, "уменьшены не ровно посаженные липы"),
        (all(o.scale < 1.0 for o in outs[1].changed), "не все уменьшены"),
    )),
    Case("play_typos", "play", ["пасади 3 берёзки у втарого подьезда"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 3, f"деревьев {len(outs[0].added_of('tree'))}, ждали 3"),
        (all(s.startswith("Береза") for s in _species(outs[0].added_of("tree"))), f"виды: {_species(outs[0].added_of('tree'))}"),
    )),
    Case("yard_bushes_at_most_20", "yard", ["посади кусты вдоль дорожек, но не больше 20"], lambda outs: expect(
        (0 < len(outs[0].added_of("bush")) <= 20, f"кустов {len(outs[0].added_of('bush'))}, ждали от 1 до 20"),
    )),
    Case("yard_undo_request", "yard", ["посади 3 дуба в центре двора", "отмени последнее"], lambda outs: expect(
        (not outs[1].added and not outs[1].removed, "«отмени» изменило план вместо подсказки"),
        ("отмен" in outs[1].result.explanation.lower(), f"нет подсказки про кнопку: {outs[1].result.explanation!r}"),
    )),
    # --- Большой реальный проект ------------------------------------------
    Case("real_remove_loungers", "real", ["убери все шезлонги"], lambda outs: expect(
        (outs[0].count(outs[0].after, "lounger") == 0, f"шезлонгов осталось {outs[0].count(outs[0].after, 'lounger')}"),
    )),
    Case("real_10_trees_biggest_area", "real", ["посади 10 деревьев в самой большой свободной области"], lambda outs: expect(
        (len(outs[0].added_of("tree")) == 10, f"деревьев {len(outs[0].added_of('tree'))}, ждали 10"),
        no_violations(outs[0]),
    )),
    Case("real_how_many_trees", "real", ["сколько на участке деревьев и кустов?"], lambda outs: expect(
        (not outs[0].added and not outs[0].removed, "вопрос изменил план"),
        ("171" in outs[0].result.explanation and "444" in outs[0].result.explanation, f"неверные числа: {outs[0].result.explanation!r}"),
    )),
]
