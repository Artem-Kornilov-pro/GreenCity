# Location 04 of 5 test DXF locations (harder than 01/02/03).
# 10 identical 15-floor tower blocks on a rectangular grid (5 columns x 2 rows),
# spaced so every building's NEAREST neighbour is 30-45m away edge-to-edge (the
# requested fire-safety band -- a uniform grid makes this trivial to guarantee:
# each building's closest neighbour is always the one directly beside/above it
# in the grid, at the fixed row/column gap, never a diagonal one).
#
# Purpose of this location: a genuinely hard case for the greenery-placement
# system. Utilities are NOT bundled -- water/sewer/heating/cable/gas each cross
# the whole territory on a different line (2 parallel lanes + an X of diagonals),
# so a lot of the open ground is inside somebody's normative setback. Combined
# with 2 parking lots, footpaths, and 10 buildings' worth of clearance, there is
# deliberately not much slack left to fit trees into.
#
# Fully synthetic (no OSM fetch) -- an exact, reproducible designed test case.
# Run from this directory: python3 build_dxf.py
# Output is written one level up, as ../04_tower_grid.dxf

import math
import numpy as np
from shapely.geometry import Polygon, Point, box, LineString
from shapely.ops import unary_union, nearest_points
import ezdxf

# --- 10 towers on a 5x2 grid ------------------------------------------------
BW, BD = 20.0, 16.0       # building footprint: 20m x 16m
GAP = 35.0                # edge-to-edge gap to the nearest neighbour (in [30,45])
COL_PITCH = BW + GAP      # 55m
ROW_Y = [0.0, BD + GAP]   # row1 at y=0..16, row2 at y=51..67

buildings = []
for row_i, y0 in enumerate(ROW_Y):
    for col_i in range(5):
        x0 = col_i * COL_PITCH
        poly = box(x0, y0, x0 + BW, y0 + BD)
        buildings.append({
            "name": f"T{row_i * 5 + col_i + 1}",
            "poly": poly,
            "parts": [poly],
            "levels": 15.0,
            "height": 15.0 * 3.0,
        })

all_buildings = unary_union([b["poly"] for b in buildings])
print(f"{len(buildings)} towers, each {BW:.0f}x{BD:.0f}m footprint, 15 floors (45m)")

# Verify the spacing requirement programmatically
min_gaps = []
for i, bi in enumerate(buildings):
    d = min(bi["poly"].distance(bj["poly"]) for j, bj in enumerate(buildings) if i != j)
    min_gaps.append(d)
print(f"Nearest-neighbour gap per building: min={min(min_gaps):.1f}m max={max(min_gaps):.1f}m (target 30-45m)")

# --- Territory: rectangle around the grid with margin for parking + utilities ---
bminx, bminy, bmaxx, bmaxy = all_buildings.bounds
MARGIN = 30.0
territory = box(bminx - MARGIN, bminy - MARGIN, bmaxx + MARGIN, bmaxy + MARGIN)
territory_angle = 0.0
territory_center = territory.centroid
tminx, tminy, tmaxx, tmaxy = territory.bounds
print(f"Territory area: {territory.area:.0f} m2")

# --- 2 parking lots: one south of row 1, one north of row 2 ---
south_parking = box(bminx - 15, tminy + 2, bmaxx + 15, bminy - 12)
north_parking = box(bminx - 15, bmaxy + 12, bmaxx + 15, tmaxy - 2)
parking_lots = [south_parking, north_parking]
parking_area = unary_union(parking_lots)
print(f"Parking area: {parking_area.area:.0f} m2 (2 lots)")

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

# --- Entrances: on the wall facing the nearest other building, robust to any
# concave shapes (none here, but keep the same reliable containment test) ---
def true_outward(poly, mid, normal_candidate, eps=0.5):
    test_pt = Point(mid[0] + normal_candidate[0] * eps, mid[1] + normal_candidate[1] * eps)
    return -normal_candidate if poly.contains(test_pt) else normal_candidate

def pick_entrances(poly, focus_pt, min_len=10.0, fracs=(0.5,)):
    ext_pts = list(poly.exterior.coords)
    n_pts = len(ext_pts) - 1
    best_i, best_score = None, None
    for i in range(n_pts):
        p0, p1 = np.array(ext_pts[i]), np.array(ext_pts[i + 1])
        elen = np.linalg.norm(p1 - p0)
        if elen < min_len:
            continue
        edir = (p1 - p0) / elen
        enormal = np.array([-edir[1], edir[0]])
        mid = (p0 + p1) / 2
        enormal = true_outward(poly, mid, enormal)
        to_focus = np.array(focus_pt) - mid
        to_focus_len = np.linalg.norm(to_focus)
        alignment = float(np.dot(enormal, to_focus / to_focus_len)) if to_focus_len > 0 else 0.0
        if best_score is None or alignment > best_score:
            best_score, best_i = alignment, i
    e0, e1 = np.array(ext_pts[best_i]), np.array(ext_pts[best_i + 1])
    evec = e1 - e0
    elen = np.linalg.norm(evec)
    edir = evec / elen
    enormal = true_outward(poly, (e0 + e1) / 2, np.array([-edir[1], edir[0]]))
    ents = []
    for frac in fracs:
        pos = e0 + edir * (elen * frac)
        ents.append({"point": Point(pos[0], pos[1]), "normal": enormal, "dir": edir})
    return ents, best_i

for b in buildings:
    nearest_other = min((o for o in buildings if o is not b), key=lambda o: b["poly"].distance(o["poly"]))
    focus = (nearest_other["poly"].centroid.x, nearest_other["poly"].centroid.y)
    b["entrances"], b["entrance_wall_idx"] = pick_entrances(b["poly"], focus)

# --- Footpaths: orthogonal routing from every entrance to the nearest parking lot ---
PATH_WIDTH = 1.8
CLEARANCE = 5.0
u_axis = np.array([1.0, 0.0])
v_axis = np.array([0.0, 1.0])
frame_origin = np.array([territory_center.x, territory_center.y])

def to_uv(pt):
    d = np.array(pt) - frame_origin
    return np.dot(d, u_axis), np.dot(d, v_axis)

def to_world(u, v):
    return tuple(frame_origin + u * u_axis + v * v_axis)

buildings_buffered = all_buildings.buffer(0.5)
paths = []
path_bend_points = []
for b in buildings:
    for ent in b["entrances"]:
        door = (ent["point"].x, ent["point"].y)
        exit_pt = (door[0] + ent["normal"][0] * CLEARANCE, door[1] + ent["normal"][1] * CLEARANCE)
        _, dest_pt = nearest_points(Point(exit_pt), parking_area)
        dest = (dest_pt.x, dest_pt.y)
        ue, ve = to_uv(exit_pt)
        ud, vd = to_uv(dest)
        candidates = [to_world(ud, ve), to_world(ue, vd)]
        chosen_bend = None
        for bend in candidates:
            seg = LineString([exit_pt, bend, dest])
            if not seg.intersects(buildings_buffered):
                chosen_bend = bend
                break
        if chosen_bend is None:
            chosen_bend = min(candidates, key=lambda bend: LineString([exit_pt, bend, dest]).intersection(buildings_buffered).length)
        route = [door, exit_pt, chosen_bend, dest]
        path_bend_points += [exit_pt, chosen_bend]
        paths.append(LineString(route).buffer(PATH_WIDTH / 2, cap_style=2, join_style=2).difference(all_buildings.buffer(0.2)))

path_area = unary_union(paths) if paths else Polygon()

# --- Utilities: water/sewer/heating/cable/gas each cross the WHOLE territory on
# a different line -- 2 parallel east-west lanes (one south of row1, one north
# of row2) plus an X of two full diagonals -- and every one of the 10 buildings
# branches off whichever trunk serves it. Deliberately scattered, not bundled:
# that's what makes tree placement hard here. ---
UNDERGROUND = [
    # (layer, depth_z, color, trunk as a LineString)
    ("WATER_SUPPLY_B1", -1.6, 5, LineString([(tminx, bminy - 6), (tmaxx, bminy - 6)])),
    ("SEWER_K1", -2.2, 30, LineString([(tminx, bmaxy + 6), (tmaxx, bmaxy + 6)])),
    ("HEATING_T1", -1.3, 1, LineString([(tminx + 5, tminy + 5), (tmaxx - 5, tmaxy - 5)])),
    ("POWER_CABLE", -0.7, 253, LineString([(tminx + 5, tmaxy - 5), (tmaxx - 5, tminy + 5)])),
]

utility_lines = []
for layer, depth_z, color, trunk in UNDERGROUND:
    t0, t1 = list(trunk.coords)
    utility_lines.append((layer, color, [(*t0, depth_z), (*t1, depth_z)]))
    for b in buildings:
        bp, tp = nearest_points(b["poly"].exterior, trunk)
        utility_lines.append((layer, color, [(tp.x, tp.y, depth_z), (bp.x, bp.y, depth_z), (bp.x, bp.y, -0.3)]))

GAS_LAYER, GAS_COLOR, GAS_Z = "GAS_PIPE", 2, 0.6
gas_trunk = LineString([(37.5, tminy + 5), (37.5, tmaxy - 5)])
gt0, gt1 = list(gas_trunk.coords)
utility_lines.append((GAS_LAYER, GAS_COLOR, [(*gt0, GAS_Z), (*gt1, GAS_Z)]))
for b in buildings:
    bp, tp = nearest_points(b["poly"].exterior, gas_trunk)
    utility_lines.append((GAS_LAYER, GAS_COLOR, [(tp.x, tp.y, GAS_Z), (bp.x, bp.y, GAS_Z), (bp.x, bp.y, b["height"] - 1.0)]))

# --- Lamps: both parking perimeters, every entrance, every footpath bend ---
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
    add_boundary_lamps(lot, 1.5, 12)
for b in buildings:
    for ent in b["entrances"]:
        lp = np.array([ent["point"].x, ent["point"].y]) + ent["normal"] * 1.5
        lamp_points.append(tuple(lp))
seen = set()
for (px, py) in path_bend_points:
    key = (round(px, 1), round(py, 1))
    if key in seen:
        continue
    seen.add(key)
    if not all_buildings.buffer(1.5).contains(Point(px, py)):
        lamp_points.append((px, py))

# Grass: everything left over inside the territory
grass_area = territory.difference(unary_union([all_buildings, parking_area, path_area]))

# --- Build DXF -----------------------------------------------------------
doc = ezdxf.new("R2010", setup=True)
doc.units = ezdxf.units.M
msp = doc.modelspace()

doc.layers.add("TERRITORY_BOUNDARY", color=8)
doc.layers.add("BUILDING_FOOTPRINT", color=1)
doc.layers.add("BUILDING_3D", color=3)
doc.layers.add("LABELS", color=7)
doc.layers.add("GRASS", color=84)
doc.layers.add("PARKING", color=253)
doc.layers.add("PARKING_MARKINGS", color=7)
doc.layers.add("LAMPS", color=2)
doc.layers.add("WATER_SUPPLY_B1", color=5)
doc.layers.add("SEWER_K1", color=30)
doc.layers.add("HEATING_T1", color=1)
doc.layers.add("POWER_CABLE", color=253)
doc.layers.add("GAS_PIPE", color=2)
doc.layers.add("ENTRANCES", color=132)
doc.layers.add("CANOPIES", color=132)
doc.layers.add("PATHS", color=9)
doc.layers.add("WINDOWS", color=150)

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
add_filled_polygon(path_area, "PATHS", 9)
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

# Buildings: footprint + extruded 3D mesh + windows + entrances/canopies
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
    win_w, win_h, sill, margin, spacing = 1.3, 1.4, 1.0, 2.0, 6.0
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

        entrance_ts = [wall_len * 0.5] if i == b["entrance_wall_idx"] else []

        usable = wall_len - 2 * margin
        n_win = max(1, int(usable // spacing)) if usable > spacing else 0
        for lvl in range(int(b["levels"])):
            z0, z1 = lvl * floor_h + sill, lvl * floor_h + sill + win_h
            for wi in range(n_win):
                t = margin + (usable / n_win) * (wi + 0.5)
                if lvl == 0 and any(abs(t - et) < 2.4 for et in entrance_ts):
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

doc.set_modelspace_vport(height=(maxy - miny + 60), center=((maxx - minx) / 2, (maxy - miny) / 2))

OUT = "../04_tower_grid.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Grass/lawn area: {grass_area.area:.0f} m2 (of {territory.area:.0f} m2 total)")
print(f"Entrances: {sum(len(b['entrances']) for b in buildings)} (with canopies)")
print(f"Footpaths: {len(paths)}, total {path_area.area:.0f} m2")
print(f"Street lamps: {len(lamp_points)}")
print(f"Underground+gas utility runs: {len(utility_lines)} (5 types x 10 buildings + 5 trunks)")
