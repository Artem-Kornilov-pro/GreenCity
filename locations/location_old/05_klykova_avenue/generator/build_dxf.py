# Location 05 of 5 test DXF locations (the largest -- a whole district).
# Loosely modelled on Prospekt Vyacheslava Klykova in Kursk's Yugo-Zapadny
# residential massif: a long avenue with apartment buildings lining both sides
# (~79 buildings total, matching the real street's published count), a school,
# two kindergartens, a church with an open square, parking pockets between
# buildings, overhead power lines running along the avenue, and underground
# utilities to every building. Fully synthetic/parametric (no OSM fetch) -- a
# designed test case at district scale, not a literal survey of the real street.
#
# Run from this directory: python3 build_dxf.py
# Output is written one level up, as ../05_klykova_avenue.dxf

import math
import random

import ezdxf
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union

random.seed(5)

# ---------------------------------------------------------------------------
# Avenue geometry
# ---------------------------------------------------------------------------
ROAD_HALF_W = 20.0     # road (both carriageways + median) is 40m wide
SETBACK = 15.0         # first row of buildings sits this far back from the road edge
NEAR_Y = ROAD_HALF_W + SETBACK   # 35m -- y of the road-facing row's road-facing wall
GAP = 20.0             # gap between adjacent columns along the avenue
GAP_DEPTH = 25.0       # gap between consecutive rows going away from the road

BUILDING_TYPES = [
    {"kind": "long", "L": 90, "D": 14, "floors": 9},
    {"kind": "tower", "L": 22, "D": 18, "floors": 15},
    {"kind": "lshape", "main_L": 55, "wing_L": 22, "D": 14, "floors": 9},
    {"kind": "long", "L": 70, "D": 14, "floors": 12},
    {"kind": "tower", "L": 24, "D": 20, "floors": 16},
]
# Depth of the residential belt behind each column, in buildings -- "примерно по
# 5 домов ... где-то есть пространство а где-то нет": mostly 4-5 deep, but some
# columns thin out to 1-2, and a few are left open entirely (0).
DEPTH_WEIGHTS = [0, 1, 2, 3, 4, 4, 5, 5, 5]

def make_building(bt, x0, near_y, row_sign):
    """near_y: y of this building's road-facing (near) wall. row_sign: +1 north, -1 south."""
    D = bt["D"]
    if bt["kind"] in ("long", "tower"):
        L = bt["L"]
        far_y = near_y + D * row_sign
        y_lo, y_hi = (near_y, far_y) if row_sign > 0 else (far_y, near_y)
        poly = box(x0, y_lo, x0 + L, y_hi)
        parts = [poly]
        length_used = L
    else:  # lshape: main wing along the road, perpendicular wing behind it
        mainL, wingL = bt["main_L"], bt["wing_L"]
        far_y = near_y + D * row_sign
        y_lo, y_hi = (near_y, far_y) if row_sign > 0 else (far_y, near_y)
        main = box(x0, y_lo, x0 + mainL, y_hi)
        wing_far = far_y + wingL * row_sign
        wy_lo, wy_hi = (far_y, wing_far) if row_sign > 0 else (wing_far, far_y)
        wing = box(x0, wy_lo, x0 + D, wy_hi)
        poly = unary_union([main, wing])
        parts = [main, wing]
        length_used = mainL
    return poly, parts, length_used, far_y

def make_entry(bt, x0, near_y, row_sign, length_used, name):
    levels = float(bt["floors"])
    height = levels * 3.0
    n_ent = 2 if length_used > 50 else 1
    fracs = (0.3, 0.7) if n_ent == 2 else (0.5,)
    normal = np.array([0.0, -1.0 * row_sign])
    entrances = []
    for f in fracs:
        ex = x0 + length_used * f
        entrances.append({"point": Point(ex, near_y), "normal": normal, "dir": np.array([1.0, 0.0])})
    return levels, height, entrances

def build_column(x0, row_sign, depth):
    """Stack `depth` buildings going away from the road at position x0. Returns
    (buildings, widest_length) -- widest_length is the widest of ALL rows in this
    column (not just row 0): the next column must clear THAT, or a long building
    picked for an inner row can overlap the next column's buildings sideways."""
    out = []
    near_y = NEAR_Y * row_sign
    widest_length = 0.0
    for d in range(depth):
        bt = random.choice(BUILDING_TYPES)
        poly, parts, length_used, far_y = make_building(bt, x0, near_y, row_sign)
        widest_length = max(widest_length, length_used)
        levels, height, entrances = make_entry(bt, x0, near_y, row_sign, length_used, "")
        out.append({
            "name": f"{'N' if row_sign > 0 else 'S'}{x0:.0f}.{d + 1}",
            "poly": poly, "parts": parts, "levels": levels, "height": height,
            "entrances": entrances, "near_y": near_y,
            "x0": x0, "x1": x0 + max(length_used, bt["D"]),
        })
        near_y = far_y + GAP_DEPTH * row_sign
    return out, (widest_length if widest_length > 0 else BUILDING_TYPES[0]["L"])

def build_belt(n_columns, row_sign, start_x=0.0):
    belt = []
    front_row = []  # depth-0 (road-facing) building of each column, for the parking-gap logic
    x = start_x
    end_x = start_x
    for _ in range(n_columns):
        depth = random.choice(DEPTH_WEIGHTS)
        col_buildings, widest_length = build_column(x, row_sign, depth)
        belt += col_buildings
        if col_buildings:
            front_row.append(col_buildings[0])
        end_x = x + widest_length
        x = end_x + GAP
    return belt, front_row, end_x

N_NORTH, N_SOUTH = 40, 39
north_row, north_front, north_end = build_belt(N_NORTH, +1)
south_row, south_front, south_end = build_belt(N_SOUTH, -1)
buildings = north_row + south_row
print(f"Residential belt: {len(north_row)} north-side buildings, {len(south_row)} south-side buildings (both depths combined)")

# ---------------------------------------------------------------------------
# Special buildings: school, 2 kindergartens, church + square -- appended past
# the end of each row so none of the 79 apartment buildings are displaced.
# ---------------------------------------------------------------------------
special = []

# School: north row continuation, one big 3-floor block + a sports ground instead of parking
school_x = north_end + GAP + 20
school_poly = box(school_x, NEAR_Y, school_x + 70, NEAR_Y + 35)
special.append({
    "name": "School", "poly": school_poly, "parts": [school_poly], "levels": 3.0, "height": 9.0,
    "entrances": [{"point": Point(school_x + 35, NEAR_Y), "normal": np.array([0.0, -1.0]), "dir": np.array([1.0, 0.0])}],
    "near_y": NEAR_Y, "x0": school_x, "x1": school_x + 70, "kind": "school",
})
sports_ground = box(school_x + 5, NEAR_Y + 40, school_x + 65, NEAR_Y + 75)

# Kindergartens: south row continuation, two small 2-floor buildings with playgrounds
kg_x0 = south_end + GAP + 20
kindergartens = []
kg_playgrounds = []
for k in range(2):
    kx = kg_x0 + k * 55
    kpoly = box(kx, -NEAR_Y - 18, kx + 32, -NEAR_Y)
    kb = {
        "name": f"Kindergarten{k + 1}", "poly": kpoly, "parts": [kpoly], "levels": 2.0, "height": 6.0,
        "entrances": [{"point": Point(kx + 16, -NEAR_Y), "normal": np.array([0.0, 1.0]), "dir": np.array([1.0, 0.0])}],
        "near_y": -NEAR_Y, "x0": kx, "x1": kx + 32, "kind": "kindergarten",
    }
    kindergartens.append(kb)
    kg_playgrounds.append(box(kx + 2, -NEAR_Y - 38, kx + 28, -NEAR_Y - 20))
special += kindergartens

# Church + square: further along the north row, past the school
church_x = school_x + 70 + GAP + 40
church_poly = unary_union([
    box(church_x, NEAR_Y + 10, church_x + 18, NEAR_Y + 34),   # nave
    box(church_x + 4, NEAR_Y + 34, church_x + 14, NEAR_Y + 44),  # apse/tower base
])
special.append({
    "name": "Church", "poly": church_poly, "parts": [box(church_x, NEAR_Y + 10, church_x + 18, NEAR_Y + 34), box(church_x + 4, NEAR_Y + 34, church_x + 14, NEAR_Y + 44)],
    "levels": 1.0, "height": 12.0,
    "entrances": [{"point": Point(church_x + 9, NEAR_Y + 10), "normal": np.array([0.0, -1.0]), "dir": np.array([1.0, 0.0])}],
    "near_y": NEAR_Y + 10, "x0": church_x, "x1": church_x + 18, "kind": "church",
})
square_area = box(church_x - 30, NEAR_Y, church_x + 55, NEAR_Y + 60).difference(church_poly.buffer(3))

buildings += special
all_buildings = unary_union([b["poly"] for b in buildings])
print(f"{len(buildings)} buildings total: {N_NORTH} north row + {N_SOUTH} south row + school + 2 kindergartens + church")

# ---------------------------------------------------------------------------
# Territory: bounding rectangle around everything, with margin
# ---------------------------------------------------------------------------
extra_geoms = unary_union([sports_ground, square_area] + kg_playgrounds)
bminx, bminy, bmaxx, bmaxy = unary_union([all_buildings, extra_geoms]).bounds
MARGIN = 30.0
road_end_x = max(north_end, south_end, church_x + 18) + MARGIN
territory = box(min(0, bminx) - MARGIN, bminy - MARGIN, max(road_end_x, bmaxx + MARGIN), bmaxy + MARGIN)
tminx, tminy, tmaxx, tmaxy = territory.bounds
print(f"Territory area: {territory.area / 1e6:.2f} km2 ({territory.area:.0f} m2), avenue length ~{road_end_x:.0f} m")

road_poly = box(tminx, -ROAD_HALF_W, tmaxx, ROAD_HALF_W)

# ---------------------------------------------------------------------------
# Parking pockets between buildings -- every 3rd gap in each row gets one, the
# rest stay open (deliberately not paving every gap: "свободное место ...
# для растительности").
# ---------------------------------------------------------------------------
def row_parking(row, row_sign):
    lots = []
    for i in range(len(row) - 1):
        if i % 3 != 1:
            continue
        b0, b1 = row[i], row[i + 1]
        gx0, gx1 = b0["x1"] + 2, b1["x0"] - 2
        if gx1 - gx0 < 12:
            continue
        if row_sign > 0:
            lot = box(gx0, ROAD_HALF_W + 2, gx1, ROAD_HALF_W + 14)
        else:
            lot = box(gx0, -ROAD_HALF_W - 14, gx1, -ROAD_HALF_W - 2)
        lots.append(lot)
    return lots

parking_lots = row_parking(north_front, +1) + row_parking(south_front, -1)
parking_area = unary_union(parking_lots) if parking_lots else Polygon()
print(f"Parking: {len(parking_lots)} pockets between buildings, {parking_area.area:.0f} m2 total")

stall_lines = []
for lot in parking_lots:
    mrr = lot.minimum_rotated_rectangle
    mc = list(mrr.exterior.coords)[:4]
    p_edges = [(mc[i], mc[(i + 1) % 4]) for i in range(4)]
    p_edges.sort(key=lambda e: -math.dist(e[0], e[1]))
    (pa0, pa1), (pb0, pb1) = p_edges[0], p_edges[1]
    n_stalls = max(1, int(math.dist(pa0, pa1) // 2.5))
    sdx, sdy = pb1[0] - pb0[0], pb1[1] - pb0[1]
    sdl = math.hypot(sdx, sdy) or 1
    sdepth = min(sdl, 5.5)
    sdx, sdy = sdx / sdl * sdepth, sdy / sdl * sdepth
    for i in range(1, n_stalls):
        t = i / n_stalls
        sx = pa0[0] + (pa1[0] - pa0[0]) * t
        sy = pa0[1] + (pa1[1] - pa0[1]) * t
        stall_lines.append(((sx, sy), (sx + sdx, sy + sdy)))

# ---------------------------------------------------------------------------
# Entrance canopies + short walkway stubs (a lightweight stand-in for full
# footpath routing, which isn't practical to solve per-entrance at this scale)
# ---------------------------------------------------------------------------
STUB_LEN = 6.0
walkways = []
for b in buildings:
    for ent in b["entrances"]:
        p0 = (ent["point"].x, ent["point"].y)
        p1 = (p0[0] + ent["normal"][0] * STUB_LEN, p0[1] + ent["normal"][1] * STUB_LEN)
        walkways.append(LineString([p0, p1]).buffer(1.2, cap_style=2))
walkway_area = unary_union(walkways).difference(all_buildings.buffer(0.2))

# ---------------------------------------------------------------------------
# Underground utilities: one trunk per side per type, in the setback lane,
# with a branch to every building on that side. Gas runs above ground with a
# riser up each facade, same convention as the other locations.
# ---------------------------------------------------------------------------
UNDERGROUND = [
    ("WATER_SUPPLY_B1", -1.6, 3.0, 5),
    ("SEWER_K1", -2.2, 5.0, 30),
    ("HEATING_T1", -1.3, 7.0, 1),
    ("POWER_CABLE", -0.7, 9.0, 253),
]
GAS_OFFSET, GAS_Z, GAS_COLOR, GAS_LAYER = 11.0, 0.6, 2, "GAS_PIPE"

utility_lines = []
rows_with_sign = [(north_row + [b for b in special if b["near_y"] > 0], +1),
                   (south_row + [b for b in special if b["near_y"] < 0], -1)]

for row, sign in rows_with_sign:
    if not row:
        continue
    rx0 = min(b["x0"] for b in row) - 10
    rx1 = max(b["x1"] for b in row) + 10
    for layer, depth_z, offset, color in UNDERGROUND:
        ty = sign * (ROAD_HALF_W + offset)
        trunk = LineString([(rx0, ty), (rx1, ty)])
        utility_lines.append((layer, color, [(rx0, ty, depth_z), (rx1, ty, depth_z)]))
        for b in row:
            bp, tp = nearest_points(b["poly"].exterior, trunk)
            utility_lines.append((layer, color, [(tp.x, tp.y, depth_z), (bp.x, bp.y, depth_z), (bp.x, bp.y, -0.3)]))
    gy = sign * (ROAD_HALF_W + GAS_OFFSET)
    gas_trunk = LineString([(rx0, gy), (rx1, gy)])
    utility_lines.append((GAS_LAYER, GAS_COLOR, [(rx0, gy, GAS_Z), (rx1, gy, GAS_Z)]))
    for b in row:
        bp, tp = nearest_points(b["poly"].exterior, gas_trunk)
        utility_lines.append((GAS_LAYER, GAS_COLOR, [(tp.x, tp.y, GAS_Z), (bp.x, bp.y, GAS_Z), (bp.x, bp.y, b["height"] - 1.0)]))

print(f"Underground+gas utility runs: {len(utility_lines)}")

# ---------------------------------------------------------------------------
# Overhead power lines (ЛЭП) running along the avenue, both sides, above ground
# ---------------------------------------------------------------------------
POLE_SPACING = 45.0
POLE_HEIGHT = 9.0
POLE_OFFSET = ROAD_HALF_W + 3.0
overhead_lines = []  # (kind, points) kind: "pole" or "span"
for sign in (+1, -1):
    py = sign * POLE_OFFSET
    n_poles = int((tmaxx - tminx) // POLE_SPACING) + 1
    pole_xs = [tminx + i * POLE_SPACING for i in range(n_poles) if tminx + i * POLE_SPACING <= tmaxx]
    for px in pole_xs:
        overhead_lines.append(("pole", [(px, py, 0), (px, py, POLE_HEIGHT)]))
    if len(pole_xs) >= 2:
        overhead_lines.append(("span", [(pole_xs[0], py, POLE_HEIGHT), (pole_xs[-1], py, POLE_HEIGHT)]))
print(f"Overhead power line poles: {sum(1 for k, _ in overhead_lines if k == 'pole')}")

# ---------------------------------------------------------------------------
# Lamps: parking perimeters + every entrance
# ---------------------------------------------------------------------------
LAMP_HEIGHT = 4.0
lamp_points = []

def add_boundary_lamps(poly, inset, spacing):
    ring = poly.buffer(-inset) if inset else poly
    if ring.is_empty:
        return
    ring = ring if isinstance(ring, Polygon) else max(ring.geoms, key=lambda g: g.area)
    coords = list(ring.exterior.coords)
    total_len = sum(math.dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
    n = max(4, int(total_len // spacing))
    step = total_len / n
    acc, target = 0.0, 0.0
    for i in range(len(coords) - 1):
        p0, p1 = coords[i], coords[i + 1]
        seg_len = math.dist(p0, p1)
        while target <= acc + seg_len:
            t = (target - acc) / seg_len if seg_len else 0
            px = p0[0] + (p1[0] - p0[0]) * t
            py = p0[1] + (p1[1] - p0[1]) * t
            if not all_buildings.buffer(1.5).contains(Point(px, py)):
                lamp_points.append((px, py))
            target += step
        acc += seg_len

for lot in parking_lots:
    add_boundary_lamps(lot, 1.5, 14)
for b in buildings:
    for ent in b["entrances"]:
        lp = np.array([ent["point"].x, ent["point"].y]) + ent["normal"] * 1.5
        lamp_points.append(tuple(lp))
print(f"Street lamps: {len(lamp_points)}")

# ---------------------------------------------------------------------------
# Grass: everything left over inside the territory
# ---------------------------------------------------------------------------
occupied = unary_union([all_buildings, parking_area, walkway_area, road_poly, sports_ground] + kg_playgrounds)
grass_area = territory.difference(occupied)
square_grass = square_area.difference(occupied)

# ---------------------------------------------------------------------------
# Build DXF
# ---------------------------------------------------------------------------
doc = ezdxf.new("R2010", setup=True)
doc.units = ezdxf.units.M
msp = doc.modelspace()

for name, color in [
    ("TERRITORY_BOUNDARY", 8), ("ROAD", 9), ("BUILDING_FOOTPRINT", 1), ("BUILDING_3D", 3),
    ("LABELS", 7), ("GRASS", 84), ("SQUARE", 3), ("PARKING", 253), ("PARKING_MARKINGS", 7),
    ("WALKWAYS", 9), ("LAMPS", 2), ("WATER_SUPPLY_B1", 5), ("SEWER_K1", 30), ("HEATING_T1", 1),
    ("POWER_CABLE", 253), ("GAS_PIPE", 2), ("POWER_LINE_OVERHEAD", 1), ("ENTRANCES", 132),
    ("CANOPIES", 132), ("WINDOWS", 150), ("SPORTS_GROUND", 30), ("PLAYGROUND", 43),
]:
    doc.layers.add(name, color=color)

minx, miny, maxx, maxy = territory.bounds
ox, oy = minx - 10, miny - 10

def shift(coords):
    return [(x - ox, y - oy) for x, y in coords]

msp.add_lwpolyline(shift(list(territory.exterior.coords)), close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})

def add_filled_polygon(geom, layer, color):
    if geom is None or geom.is_empty:
        return
    geoms = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
    for g in geoms:
        if g.is_empty or g.area < 1:
            continue
        ext = shift(list(g.exterior.coords))
        msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": layer})
        hatch = msp.add_hatch(color=color, dxfattribs={"layer": layer})
        hatch.paths.add_polyline_path(ext, is_closed=True)
        for interior in g.interiors:
            hatch.paths.add_polyline_path(shift(list(interior.coords)), is_closed=True, flags=ezdxf.const.BOUNDARY_PATH_OUTERMOST)

add_filled_polygon(grass_area, "GRASS", 84)
add_filled_polygon(road_poly, "ROAD", 9)
add_filled_polygon(square_grass, "SQUARE", 100)
add_filled_polygon(sports_ground, "SPORTS_GROUND", 30)
for pg in kg_playgrounds:
    add_filled_polygon(pg, "PLAYGROUND", 43)
add_filled_polygon(walkway_area, "WALKWAYS", 9)
add_filled_polygon(parking_area, "PARKING", 253)

for (sp0, sp1) in stall_lines:
    msp.add_line(shift([sp0])[0], shift([sp1])[0], dxfattribs={"layer": "PARKING_MARKINGS"})

for (x, y) in lamp_points:
    sx, sy = x - ox, y - oy
    msp.add_line((sx, sy, 0), (sx, sy, LAMP_HEIGHT), dxfattribs={"layer": "LAMPS"})
    msp.add_circle(center=(sx, sy, LAMP_HEIGHT), radius=0.35, dxfattribs={"layer": "LAMPS"})

for layer, color, pts in utility_lines:
    shifted = [(x - ox, y - oy, z) for x, y, z in pts]
    if len(shifted) == 2:
        msp.add_line(shifted[0], shifted[1], dxfattribs={"layer": layer, "color": color})
    else:
        msp.add_polyline3d(shifted, dxfattribs={"layer": layer, "color": color})

for kind, pts in overhead_lines:
    shifted = [(x - ox, y - oy, z) for x, y, z in pts]
    msp.add_line(shifted[0], shifted[1], dxfattribs={"layer": "POWER_LINE_OVERHEAD"})
    if kind == "pole":
        msp.add_circle(center=shifted[1], radius=0.2, dxfattribs={"layer": "POWER_LINE_OVERHEAD"})

def true_outward(poly, mid, normal_candidate, eps=0.5):
    test_pt = Point(mid[0] + normal_candidate[0] * eps, mid[1] + normal_candidate[1] * eps)
    return -normal_candidate if poly.contains(test_pt) else normal_candidate

# Buildings: footprint + extruded 3D mesh (per convex part) + windows + entrances/canopies
for b in buildings:
    poly = b["poly"]
    ext = shift(list(poly.exterior.coords))
    msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": "BUILDING_FOOTPRINT"})
    h = b["height"]
    n = len(ext) - 1
    base = ext[:n]

    for part in b["parts"]:
        p_ext = shift(list(part.exterior.coords))
        pn = len(p_ext) - 1
        p_base = p_ext[:pn]
        mesh = msp.add_mesh(dxfattribs={"layer": "BUILDING_3D"})
        with mesh.edit_data() as mesh_data:
            verts = [(x, y, 0.0) for x, y in p_base] + [(x, y, h) for x, y in p_base]
            mesh_data.vertices = verts
            faces = [list(range(pn)), list(range(2 * pn - 1, pn - 1, -1))]
            for i in range(pn):
                j = (i + 1) % pn
                faces.append([i, j, pn + j, pn + i])
            mesh_data.faces = faces

    bcx, bcy = poly.centroid.x - ox, poly.centroid.y - oy
    floor_h = h / b["levels"]
    win_w, win_h, sill, margin, spacing = 1.3, 1.4, 1.0, 2.0, 8.0
    ent_world_ts = [ent["point"].x for ent in b["entrances"]]
    for i in range(n):
        w0, w1 = np.array(base[i]), np.array(base[(i + 1) % n])
        wall_vec = w1 - w0
        wall_len = np.linalg.norm(wall_vec)
        if wall_len < 10.0:
            continue
        wall_dir = wall_vec / wall_len
        wall_normal = np.array([-wall_dir[1], wall_dir[0]])
        wmid = (w0 + w1) / 2
        wmid_world = (wmid[0] + ox, wmid[1] + oy)
        wall_normal = true_outward(poly, wmid_world, wall_normal)

        # is this the road-facing (entrance) wall? -- its midpoint's y matches near_y
        on_entrance_wall = abs(wmid_world[1] - b["near_y"]) < 0.5
        local_entrance_ts = []
        if on_entrance_wall:
            w0_world_x = w0[0] + ox
            local_entrance_ts = [ex - w0_world_x for ex in ent_world_ts]

        usable = wall_len - 2 * margin
        n_win = max(1, int(usable // spacing)) if usable > spacing else 0
        for lvl in range(int(b["levels"])):
            z0, z1 = lvl * floor_h + sill, lvl * floor_h + sill + win_h
            for wi in range(n_win):
                t = margin + (usable / n_win) * (wi + 0.5)
                if lvl == 0 and any(abs(t - et) < 2.4 for et in local_entrance_ts):
                    continue
                c = w0 + wall_dir * t + wall_normal * 0.05
                p_left = c - wall_dir * win_w / 2
                p_right = c + wall_dir * win_w / 2
                msp.add_3dface([(*p_left, z0), (*p_right, z0), (*p_right, z1), (*p_left, z1)], dxfattribs={"layer": "WINDOWS"})

    canopy_h = min(floor_h - 0.3, 3.2)
    canopy_depth, canopy_width = 1.4, 2.6
    for ent in b["entrances"]:
        dx, dy = ent["point"].x - ox, ent["point"].y - oy
        msp.add_circle(center=(dx, dy, 0.05), radius=0.5, dxfattribs={"layer": "ENTRANCES"})
        inner = np.array([dx, dy])
        outer = inner + ent["normal"] * canopy_depth
        p1 = inner - ent["dir"] * canopy_width / 2
        p2 = inner + ent["dir"] * canopy_width / 2
        p3 = outer + ent["dir"] * canopy_width / 2
        p4 = outer - ent["dir"] * canopy_width / 2
        msp.add_3dface([(*p1, canopy_h), (*p2, canopy_h), (*p3, canopy_h), (*p4, canopy_h)], dxfattribs={"layer": "CANOPIES"})
        msp.add_3dface([(*p3, canopy_h), (*p4, canopy_h), (*p4, canopy_h - 0.25), (*p3, canopy_h - 0.25)], dxfattribs={"layer": "CANOPIES"})

    msp.add_text(f"{b['name']} h={h:.1f}m", dxfattribs={"layer": "LABELS", "height": 2.5, "insert": (bcx, bcy)})

doc.set_modelspace_vport(height=(maxy - miny + 100), center=((maxx - minx) / 2, (maxy - miny) / 2))

OUT = "../05_klykova_avenue.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Grass/lawn+square area: {grass_area.area + square_grass.area:.0f} m2")
