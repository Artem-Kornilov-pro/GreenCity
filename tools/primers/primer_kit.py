"""
Набор для сборки синтетических эталонных участков GreenPlan (locations/2x_*_primer
и 30_*): геометрия участка, зоны, здания, посадки и МАФ -> DXF в тех же
соглашениях по слоям, что читает parser/dxf_parsing/rules.py.

Каждая посадка и каждый МАФ проверяются ДО записи по тем же нормам, что и
бэкенд (core/setback_norms.setback_for, в том числе правила по породе):
то, что нарушает отступ, в эталон не попадает. Эталон -- образец решения,
поэтому в нём не должно быть ни одного нарушения (это проверяет тест
backend/tests/greenplan/test_primers.py).

Особенность парсера: полигоны одного слоя склеиваются, дырки теряются. Поэтому
каждый кусок дорожки (дуга кольца, луч, отрезок сетки) пишется на СВОЙ слой
и сам по себе без дырок -- иначе кольцевая дорожка стала бы сплошным кругом.
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.plant_catalog import load_catalog  # noqa: E402
from core.setback_norms import setback_for  # noqa: E402

# Слой-префикс -> (тип зоны, minDistance) -- зеркало POLYGON_RULES парсера.
ZONE_KINDS: dict[str, tuple[str, float]] = {
    "BUILDING": ("building", 1.5),
    "ROAD": ("road", 1.0),
    "PARKING": ("custom", 1.0),
    "PLAYGROUND": ("playground_zone", 1.0),
    "GAS_PIPE": ("gas_pipeline", 2.0),
    "SEWER": ("sewer", 3.0),
    "WATER_SUPPLY": ("water_pipeline", 3.0),
    "CABLE_COMM": ("signal_cable", 0.5),
    "HEATING": ("heat_network", 2.0),
    "PATH": ("pedestrian_path", 0.5),
}
FURNITURE_LAYERS = {"lamp": "LAMPS", "bench": "BENCH", "urn": "URN", "bike_rack": "BIKE_RACK", "entrance": "ENTRANCE"}

SITE_CLEARANCE_M = 0.5  # как Placer: не вплотную к границе
FURNITURE_ZONE_CLEARANCE_M = 0.3  # как Placer.FURNITURE_CLEARANCE_M
# Нормы между точечными объектами (core/placement.POINT_CLEARANCE_M + здравый смысл).
LAMP_TREE_M = 4.0  # СП 42 табл. 9.1: опора освещения -- дерево
ENTRANCE_CLEARANCE_M = {"tree": 5.0, "bush": 2.5}
TREE_TREE_M = 3.0
BUSH_BUSH_M = 0.9
PLANT_FURNITURE_M = {"tree": 1.5, "bush": 1.0}

_CATALOG = {item.label: item for item in load_catalog() if item.category in ("tree", "bush")}


def rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def strip(points: list[tuple[float, float]], width: float) -> Polygon:
    """Полоса вдоль ломаной (дорожка, сеть) с плоскими торцами."""
    return LineString(points).buffer(width / 2, cap_style="flat", join_style="round")


def circle(cx: float, cy: float, r: float, n: int = 72) -> Polygon:
    return Polygon([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)])


def arc_band(cx: float, cy: float, r0: float, r1: float, a0: float, a1: float, n: int = 24) -> Polygon:
    """Кусок кольца между углами a0..a1 (градусы) -- без дырки."""
    outer = [(cx + r1 * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r1 * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
    inner = [(cx + r0 * math.cos(math.radians(a1 - (a1 - a0) * i / n)), cy + r0 * math.sin(math.radians(a1 - (a1 - a0) * i / n))) for i in range(n + 1)]
    return Polygon(outer + inner)


def along(points: list[tuple[float, float]], step: float, start: float | None = None):
    """(x, y, tx, ty) через step вдоль ломаной; start -- смещение первой точки."""
    line = LineString(points)
    d = step / 2 if start is None else start
    while d <= line.length:
        p = line.interpolate(d)
        a, b = line.interpolate(max(d - 0.5, 0)), line.interpolate(min(d + 0.5, line.length))
        norm = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        yield p.x, p.y, (b.x - a.x) / norm, (b.y - a.y) / norm
        d += step


@dataclass
class Primer:
    slug: str
    boundary: Polygon
    zones: list[tuple[str, BaseGeometry]] = field(default_factory=list)  # (слой, полигон)
    buildings: list[tuple[Polygon, float, str]] = field(default_factory=list)
    plants: list[tuple[str, str, float, float]] = field(default_factory=list)  # (вид, tree/bush, x, y)
    furniture: list[tuple[str, float, float, float]] = field(default_factory=list)  # (тип, x, y, поворот°)
    curbs: list[list[tuple[float, float]]] = field(default_factory=list)
    lawns: list[Polygon] = field(default_factory=list)
    rejected: int = 0
    reasons: Counter = field(default_factory=Counter)  # почему отброшено -- для подгонки раскладки

    # --- Геометрия ------------------------------------------------------------

    def zone(self, layer: str, geom: BaseGeometry) -> None:
        assert any(layer.startswith(prefix) for prefix in ZONE_KINDS), layer
        if geom.geom_type == "Polygon":
            assert not geom.interiors, f"{layer}: зона с дыркой -- парсер её потеряет"
        self.zones.append((layer, geom))

    def building(self, poly: Polygon, height: float, label: str) -> None:
        self.buildings.append((poly, height, label))
        self.zones.append(("BUILDING", poly))

    def entrance(self, x: float, y: float) -> None:
        self.furniture.append(("entrance", x, y, 0.0))

    def lawn(self, poly: Polygon) -> None:
        self.lawns.append(poly)

    def curb(self, points: list[tuple[float, float]]) -> None:
        self.curbs.append(points)

    # --- Проверка норм --------------------------------------------------------

    def _zone_kind(self, layer: str) -> tuple[str, float]:
        return next(kind for prefix, kind in ZONE_KINDS.items() if layer.startswith(prefix))

    def _inside_site(self, pt: Point) -> bool:
        return self.boundary.contains(pt) and self.boundary.exterior.distance(pt) >= SITE_CLEARANCE_M

    def plant(self, species: str, x: float, y: float) -> bool:
        item = _CATALOG[species]
        kind = item.category
        pt = Point(x, y)
        ok = self._inside_site(pt)
        reason = "за границей"
        for layer, geom in self.zones if ok else []:
            zone_type, min_distance = self._zone_kind(layer)
            need = setback_for(zone_type, kind, min_distance, species=species)
            if geom.contains(pt) or geom.distance(pt) < need + 0.05:
                ok, reason = False, f"{kind}: {layer}"
                break
        if ok:
            for ftype, fx, fy, _ in self.furniture:
                d = math.hypot(fx - x, fy - y)
                need = ENTRANCE_CLEARANCE_M[kind] if ftype == "entrance" else LAMP_TREE_M if (ftype == "lamp" and kind == "tree") else PLANT_FURNITURE_M[kind]
                if d < need:
                    ok, reason = False, f"{kind}: {ftype}"
                    break
        if ok:
            for _, other_kind, px, py in self.plants:
                d = math.hypot(px - x, py - y)
                if d < (TREE_TREE_M if kind == other_kind == "tree" else 2.0 if "tree" in (kind, other_kind) else BUSH_BUSH_M):
                    ok, reason = False, f"{kind}: рядом {other_kind}"
                    break
        if ok:
            self.plants.append((species, kind, x, y))
        else:
            self.rejected += 1
            self.reasons[reason] += 1
        return ok

    def put(self, ftype: str, x: float, y: float, rotation: float = 0.0) -> bool:
        """МАФ: не в зонах (и не ближе FURNITURE_ZONE_CLEARANCE_M), фонарь --
        не ближе 4 м к деревьям, остальное -- с зазором от посадок."""
        pt = Point(x, y)
        ok, reason = self._inside_site(pt), f"{ftype}: за границей"
        for layer, geom in self.zones if ok else []:
            if geom.contains(pt) or geom.distance(pt) < FURNITURE_ZONE_CLEARANCE_M:
                ok, reason = False, f"{ftype}: {layer}"
                break
        if ok:
            for _, kind, px, py in self.plants:
                need = LAMP_TREE_M if (ftype == "lamp" and kind == "tree") else PLANT_FURNITURE_M[kind]
                if math.hypot(px - x, py - y) < need:
                    ok, reason = False, f"{ftype}: рядом {kind}"
                    break
        if ok and any(math.hypot(fx - x, fy - y) < 1.0 for _, fx, fy, _ in self.furniture):
            ok, reason = False, f"{ftype}: рядом МАФ"
        if ok:
            self.furniture.append((ftype, x, y, rotation))
        else:
            self.rejected += 1
            self.reasons[reason] += 1
        return ok

    def bench_with_urn(self, x: float, y: float, tx: float, ty: float, face: float) -> None:
        """Скамейка у дорожки, развёрнутая к ней (face -- угол, °), урна рядом
        вдоль дорожки -- как у каждой скамейки площадки отдыха."""
        if self.put("bench", x, y, face):
            self.put("urn", x + tx * 1.3, y + ty * 1.3)

    # --- DXF ------------------------------------------------------------------

    def write(self) -> Path:
        doc = ezdxf.new("R2018", setup=False)
        doc.header["$INSUNITS"] = 6  # метры
        msp = doc.modelspace()

        def layer(name: str, color: int = 7) -> str:
            if name not in doc.layers:
                doc.layers.add(name, color=color)
            return name

        msp.add_lwpolyline(list(self.boundary.exterior.coords)[:-1], close=True, dxfattribs={"layer": layer("TERRITORY_BOUNDARY", 8)})
        for poly in self.lawns:
            msp.add_lwpolyline(list(poly.exterior.coords)[:-1], close=True, dxfattribs={"layer": layer("GRASS", 84)})
        for name, geom in self.zones:
            if name == "BUILDING":
                continue
            parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                msp.add_lwpolyline(list(part.exterior.coords)[:-1], close=True, dxfattribs={"layer": layer(name, 9)})
        for poly, height, label in self.buildings:
            ring = list(poly.exterior.coords)[:-1]
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer("BUILDINGS_FOOTPRINT", 1)})
            mesh = msp.add_mesh(dxfattribs={"layer": layer("BUILDINGS_3D", 3)})
            n = len(ring)
            with mesh.edit_data() as data:
                data.vertices = [(x, y, 0.0) for x, y in ring] + [(x, y, height) for x, y in ring]
                data.faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))] + [[k, (k + 1) % n, n + (k + 1) % n, n + k] for k in range(n)]
            c = poly.centroid
            msp.add_text(label, dxfattribs={"layer": layer("LABELS", 7), "height": 1.5, "insert": (c.x, c.y)})
        for points in self.curbs:
            msp.add_lwpolyline(points, dxfattribs={"layer": layer("CURB", 9)})
        for species, _, x, y in self.plants:
            msp.add_point((x, y, 0.0), dxfattribs={"layer": layer(species.upper(), 3)})
        for ftype, x, y, rotation in self.furniture:
            if ftype == "bench":
                # Скамейка -- блок, чтобы поворот дошёл до сцены (у POINT его нет).
                if "BENCH_BLOCK" not in doc.blocks:
                    block = doc.blocks.new("BENCH_BLOCK")
                    block.add_lwpolyline([(-0.75, -0.25), (0.75, -0.25), (0.75, 0.25), (-0.75, 0.25)], close=True)
                msp.add_blockref("BENCH_BLOCK", (x, y), dxfattribs={"layer": layer("BENCH", 30), "rotation": rotation})
            else:
                msp.add_point((x, y, 0.0), dxfattribs={"layer": layer(FURNITURE_LAYERS[ftype], 2)})

        path = ROOT / "locations" / self.slug / f"{self.slug}.dxf"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(path)
        return path
