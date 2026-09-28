"""Общие примитивы генераторов тестовых участков locations/location_test/.

Всё в метрах ($INSUNITS = 6), слои -- те же, что у locations/location_old/
(TERRITORY_BOUNDARY, BUILDING_FOOTPRINT/BUILDING_3D, WATER_SUPPLY_B1, ...),
их распознаёт parser/dxf_parsing/rules.py. Существующие деревья и кусты -- на
слоях EXISTING_TREE_<вид>/EXISTING_BUSH_<вид>: вид берётся парсером из имени
слоя по справочнику data/plant_archetypes/species_catalog.json.

Площадные слои пишутся кусками без дыр: парсер объединяет все контуры слоя
(и LWPOLYLINE, и контуры HATCH) и сам режет результат по дырам, а вырез
внутри одного контура он не отличает от второго контура и залил бы его.
"""

from __future__ import annotations

import math

import ezdxf
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

LAYERS = {
    "TERRITORY_BOUNDARY": 8,
    "BUILDING_FOOTPRINT": 1,
    "BUILDING_3D": 9,
    "WINDOWS": 150,
    "CANOPIES": 132,
    "ENTRANCES": 132,
    "LABELS": 7,
    "ROAD": 251,
    "ROAD_MARKINGS": 7,
    "CURB": 8,
    "SIDEWALK": 31,
    "PATHS": 33,
    "PARKING": 253,
    "PARKING_MARKINGS": 7,
    "PLAYGROUND": 40,
    "PLAYGROUND_SPORTS": 42,
    "GRASS": 84,
    "TRANSFORMER_SUBSTATION": 6,
    "WATER_SUPPLY_B1": 5,
    "SEWER_K1": 30,
    "HEATING_T1": 1,
    "GAS_PIPE": 2,
    "POWER_CABLE": 212,
    "CABLE_COMM": 150,
    "OVERHEAD_POWER_LINE": 6,
    "LAMPS": 2,
    "BENCHES": 34,
}

# minDistance зоны слоя -- как в POLYGON_RULES парсера. Редактор подсвечивает
# красным любой объект внутри зоны или ближе minDistance к ней (фонарь,
# скамейку -- тоже), см. frontend/src/geometry.ts::violatesAt; is_clear ниже
# проверяет то же самое заранее, при расстановке.
ZONE_MIN_DISTANCE = {
    "BUILDING_FOOTPRINT": 1.5, "TRANSFORMER_SUBSTATION": 2.0, "ROAD": 1.0, "PARKING": 1.0,
    "PLAYGROUND": 1.0, "PLAYGROUND_SPORTS": 1.0, "GAS_PIPE": 2.0, "SEWER_K1": 3.0,
    "WATER_SUPPLY_B1": 3.0, "CABLE_COMM": 0.5, "POWER_CABLE": 2.0, "OVERHEAD_POWER_LINE": 2.0,
    "HEATING_T1": 2.0, "PATHS": 0.5, "SIDEWALK": 0.5,
}

HATCH_COLORS = {"ROAD": 251, "SIDEWALK": 31, "PATHS": 33, "PARKING": 253, "PLAYGROUND": 40, "PLAYGROUND_SPORTS": 42, "GRASS": 84}

# Глубина заложения сетей, м (Z трассы в DXF). Сама глубина парсеру не нужна
# -- охранная зона считается в плане, -- но в CAD и 3D трассы видны на своём
# уровне, как в location_old.
DEPTH = {"WATER_SUPPLY_B1": -1.8, "SEWER_K1": -2.6, "HEATING_T1": -1.4, "GAS_PIPE": -1.2, "POWER_CABLE": -0.7, "CABLE_COMM": -0.6}


def polygons(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]


def split_holes(poly: Polygon) -> list[Polygon]:
    """Куски без дыр: вертикальный разрез через центр первой дыры, рекурсивно."""
    if not poly.interiors:
        return [poly]
    x = poly.interiors[0].centroid.x
    minx, miny, maxx, maxy = poly.bounds
    out = []
    for half in (box(minx - 1, miny - 1, x, maxy + 1), box(x, miny - 1, maxx + 1, maxy + 1)):
        for part in polygons(poly.intersection(half)):
            out += split_holes(part)
    return out


class Site:
    def __init__(self):
        self.doc = ezdxf.new("R2010", setup=True)
        self.doc.units = ezdxf.units.M
        self.msp = self.doc.modelspace()
        for name, color in LAYERS.items():
            self.doc.layers.add(name, color=color)
        self.stats: dict[str, int] = {}
        # (геометрия зоны так, как её построит парсер, её minDistance)
        self.zones: list[tuple[BaseGeometry, float]] = []

    def _zone(self, geom, layer):
        if layer in ZONE_MIN_DISTANCE:
            self.zones.append((geom, ZONE_MIN_DISTANCE[layer]))

    def is_clear(self, x, y, margin=0.2) -> bool:
        """Не подсветит ли редактор объект в этой точке как нарушение."""
        p = Point(x, y)
        return all(geom.distance(p) >= d + margin for geom, d in self.zones)

    def _count(self, key, n=1):
        self.stats[key] = self.stats.get(key, 0) + n

    def layer(self, name, color=7):
        if name not in self.doc.layers:
            self.doc.layers.add(name, color=color)
        return name

    # --- площадные слои -------------------------------------------------------
    def boundary(self, poly: Polygon):
        self.msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
        self.boundary_poly = poly

    def area(self, geom: BaseGeometry, layer: str, min_area=1.0):
        """Заливка площадного слоя: контур LWPOLYLINE + HATCH, куски без дыр."""
        for poly in polygons(geom):
            for piece in split_holes(poly.buffer(0)):
                if piece.area < min_area:
                    continue
                pts = list(piece.exterior.coords)[:-1]
                self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
                hatch = self.msp.add_hatch(color=HATCH_COLORS.get(layer, 7), dxfattribs={"layer": layer})
                hatch.paths.add_polyline_path(pts, is_closed=True)
                self._zone(piece, layer)
                self._count(layer)

    def curbs(self, geom: BaseGeometry):
        for poly in polygons(geom):
            self.msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": "CURB"})

    def parking(self, rect: Polygon, stall=2.5, depth=5.3):
        """Стоянка с разметкой мест поперёк длинной стороны прямоугольника."""
        self.area(rect, "PARKING")
        minx, miny, maxx, maxy = rect.bounds
        horizontal = (maxx - minx) >= (maxy - miny)
        length = (maxx - minx) if horizontal else (maxy - miny)
        n = int(length // stall)
        for i in range(1, n):
            t = i * length / n
            if horizontal:
                a, b = (minx + t, miny), (minx + t, miny + min(depth, maxy - miny))
            else:
                a, b = (minx, miny + t), (minx + min(depth, maxx - minx), miny + t)
            self.msp.add_line(a, b, dxfattribs={"layer": "PARKING_MARKINGS"})
        self._count("parking_stalls", n)

    # --- сети -------------------------------------------------------------------
    def utility(self, layer: str, pts, z=None):
        """Трасса сети: открытая 3D-полилиния на глубине заложения."""
        z = DEPTH.get(layer, -1.0) if z is None else z
        self.msp.add_polyline3d([(x, y, z) for x, y in pts], dxfattribs={"layer": layer})
        # Парсер раздувает трассу в коридор шириной 2*minDistance с плоскими торцами.
        self._zone(LineString(pts).buffer(ZONE_MIN_DISTANCE[layer], cap_style=2), layer)
        self._count(layer)

    def overhead_line(self, pts, height=9.0, pole_step=35.0):
        """Воздушная ЛЭП: провод на высоте + опоры (вертикальные отрезки)."""
        self._zone(LineString(pts).buffer(ZONE_MIN_DISTANCE["OVERHEAD_POWER_LINE"], cap_style=2), "OVERHEAD_POWER_LINE")
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            self.msp.add_line((x1, y1, height), (x2, y2, height), dxfattribs={"layer": "OVERHEAD_POWER_LINE"})
            seg = math.hypot(x2 - x1, y2 - y1)
            for i in range(int(seg // pole_step) + 1):
                t = i * pole_step / seg
                px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                self.msp.add_line((px, py, 0), (px, py, height), dxfattribs={"layer": "OVERHEAD_POWER_LINE"})
        self._count("OVERHEAD_POWER_LINE")

    # --- точечные объекты -------------------------------------------------------
    def lamp(self, x, y, height=5.0):
        """Фонарь как в location_old: столб LINE + плафон CIRCLE на верхушке."""
        self.msp.add_line((x, y, 0), (x, y, height), dxfattribs={"layer": "LAMPS"})
        self.msp.add_circle((x, y, height), radius=0.35, dxfattribs={"layer": "LAMPS"})
        self._count("lamps")

    def bench(self, x, y, angle_deg=0.0):
        if "BENCH" not in self.doc.blocks:
            blk = self.doc.blocks.new("BENCH")
            blk.add_lwpolyline([(-0.9, -0.25), (0.9, -0.25), (0.9, 0.25), (-0.9, 0.25)], close=True)
        self.msp.add_blockref("BENCH", (x, y), dxfattribs={"layer": "BENCHES", "rotation": angle_deg})
        self._count("benches")

    def plant(self, kind: str, species: str, x, y, crown: float):
        """Существующее дерево/куст: POINT (сам объект) + CIRCLE кроны (видно в CAD).
        Круг ровно в точке POINT парсер считает маркером той же посадки."""
        layer = self.layer(f"EXISTING_{kind}_{species}", 3 if kind == "TREE" else 82)
        self.msp.add_point((x, y, 0), dxfattribs={"layer": layer})
        self.msp.add_circle((x, y), radius=crown, dxfattribs={"layer": layer})
        self._count(f"existing_{kind.lower()}")

    def crosswalk(self, x, y, width, length, horizontal=True, stripe=0.5, gap=0.6):
        """Зебра -- только разметка на ROAD_MARKINGS (парсер её пропускает).
        Точечный объект CROSSWALK не ставится: в каталоге редактора его нет, и
        он рисовался бы серым кубиком посреди проезжей части."""
        n = int(width // (stripe + gap))
        for i in range(n):
            o = -width / 2 + i * (stripe + gap)
            if horizontal:
                r = box(x + o, y - length / 2, x + o + stripe, y + length / 2)
            else:
                r = box(x - length / 2, y + o, x + length / 2, y + o + stripe)
            self.msp.add_lwpolyline(list(r.exterior.coords)[:-1], close=True, dxfattribs={"layer": "ROAD_MARKINGS"})

    def label(self, text, x, y, height=2.0, rotation=0.0):
        self.msp.add_text(text, dxfattribs={"layer": "LABELS", "height": height, "insert": (x, y), "rotation": rotation})

    # --- здания -----------------------------------------------------------------
    def _prism(self, layer, poly: Polygon, h):
        pts = list(poly.exterior.coords)[:-1]
        n = len(pts)
        mesh = self.msp.add_mesh(dxfattribs={"layer": layer})
        with mesh.edit_data() as md:
            md.vertices = [(x, y, 0.0) for x, y in pts] + [(x, y, h) for x, y in pts]
            faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
            faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
            md.faces = faces

    def building(self, parts: list[Polygon], levels: int, name: str, entrances=(), floor_h=3.0, windows=True, win_step=3.6):
        """Здание: контур, объём (по выпуклым частям -- вогнутую грань часть
        CAD-просмотрщиков триангулирует с лишней диагональю), окна, входы с
        козырьками, подпись. entrances -- точки на стенах (x, y)."""
        poly = unary_union(parts)
        assert isinstance(poly, Polygon), f"{name}: части здания должны соприкасаться"
        poly = poly.simplify(0.01)
        self._zone(poly, "BUILDING_FOOTPRINT")
        h = levels * floor_h
        self.msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": "BUILDING_FOOTPRINT"})
        for part in parts:
            self._prism("BUILDING_3D", part, h)

        ents = []
        for ex, ey in entrances:
            p = Point(ex, ey)
            ring = list(poly.exterior.coords)
            best = min(range(len(ring) - 1), key=lambda i: p.distance(LineString([ring[i], ring[i + 1]])))
            a, b = np.array(ring[best]), np.array(ring[best + 1])
            d = (b - a) / np.linalg.norm(b - a)
            nrm = np.array([-d[1], d[0]])
            if poly.contains(Point(ex + nrm[0] * 0.5, ey + nrm[1] * 0.5)):
                nrm = -nrm
            ents.append((np.array([ex, ey]), d, nrm))
            canopy_h = min(floor_h - 0.3, 3.0)
            inner, outer = np.array([ex, ey]), np.array([ex, ey]) + nrm * 1.4
            p1, p2 = inner - d * 1.3, inner + d * 1.3
            p3, p4 = outer + d * 1.3, outer - d * 1.3
            self.msp.add_3dface([(*p1, canopy_h), (*p2, canopy_h), (*p3, canopy_h), (*p4, canopy_h)], dxfattribs={"layer": "CANOPIES"})
            self.msp.add_circle((ex, ey, 0.05), radius=0.5, dxfattribs={"layer": "ENTRANCES"})
        self._count("entrances", len(ents))

        if windows:
            ring = list(poly.exterior.coords)
            for i in range(len(ring) - 1):
                a, b = np.array(ring[i]), np.array(ring[i + 1])
                wall = np.linalg.norm(b - a)
                if wall < 6:
                    continue
                d = (b - a) / wall
                nrm = np.array([-d[1], d[0]])
                mid = (a + b) / 2
                if poly.contains(Point(*(mid + nrm * 0.5))):
                    nrm = -nrm
                n_win = int((wall - 2) // win_step)
                door_ts = [float(np.dot(e[0] - a, d)) for e in ents if abs(float(np.dot(e[0] - a, nrm))) < 0.5]
                for lvl in range(levels):
                    z0, z1 = lvl * floor_h + 0.9, lvl * floor_h + 0.9 + min(1.5, floor_h - 1.2)
                    for k in range(n_win):
                        t = 1 + (wall - 2) * (k + 0.5) / n_win
                        if lvl == 0 and any(abs(t - dt) < 2.2 for dt in door_ts):
                            continue
                        c = a + d * t + nrm * 0.05
                        left, right = c - d * 0.7, c + d * 0.7
                        self.msp.add_3dface([(*left, z0), (*right, z0), (*right, z1), (*left, z1)], dxfattribs={"layer": "WINDOWS"})
        # Подпись -- по центру, вдоль длинной стороны здания.
        # Ширина символа -- около половины высоты шрифта; у маленьких зданий
        # (ЦТП) шрифт уменьшается, чтобы подпись не вылезала за контур.
        text = f"{name}, {levels} эт."
        minx, miny, maxx, maxy = poly.bounds
        vertical = maxy - miny > maxx - minx
        th = min(2.2, 1.7 * max(maxx - minx, maxy - miny) / len(text))
        half = len(text) * 0.25 * th
        c = poly.representative_point()
        if vertical:
            self.label(text, c.x + th / 2, c.y - half, height=th, rotation=90)
        else:
            self.label(text, c.x - half, c.y - th / 2, height=th)
        self._count("buildings")
        return poly

    def small_structure(self, poly: Polygon, layer, h, text):
        """ТП и подобное: контур на площадном слое + объём + подпись."""
        self.msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": layer})
        self._zone(poly, layer)
        self._prism(layer, poly, h)
        c = poly.centroid
        self.label(text, c.x - 1.5, c.y, height=1.5)

    def save(self, path):
        minx, miny, maxx, maxy = self.boundary_poly.bounds
        self.doc.set_modelspace_vport(height=(maxy - miny) * 1.2, center=((minx + maxx) / 2, (miny + maxy) / 2))
        self.doc.saveas(path)
        print(f"Saved {path}")
        for k, v in sorted(self.stats.items()):
            print(f"  {k}: {v}")
