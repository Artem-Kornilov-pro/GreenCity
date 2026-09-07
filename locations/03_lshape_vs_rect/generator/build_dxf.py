# Location 03 of 5 test DXF locations (more complex than 01/02).
# Two buildings facing each other across a yard:
#   A) a Г-shaped (L-shaped) building -- a long main wing + a shorter perpendicular
#      wing -- 12 floors (the taller of the two)
#   B) a plain rectangular building opposite it, 8 floors (shorter)
# Entrances (with canopies) on both buildings open onto the shared yard. Utilities
# are deliberately NOT bundled into one corridor: water/sewer/heating/cable/gas
# each enter the territory from a different edge and cross the open yard on their
# own route to both buildings, so the greenery-placement system actually has to
# route trees around real, scattered obstacles (that's the point of this location).
# Also: a transformer substation (outside the yard, on the street side) and a
# small playground (inside the yard).
#
# This location is fully synthetic (no OSM fetch) -- a designed test case, not a
# real-world footprint -- built directly in local metres so its geometry is exact
# and reproducible. Run from this directory: python3 build_dxf.py
# Output is written one level up, as ../03_lshape_vs_rect.dxf

import math
import numpy as np
from shapely.geometry import Polygon, Point, box, LineString
from shapely.ops import unary_union, nearest_points
from shapely.affinity import rotate
import ezdxf

# --- Buildings ---------------------------------------------------------------
a_main = box(0, 60, 60, 72)     # main wing: 60m x 12m
a_wing = box(0, 30, 12, 60)     # shorter perpendicular wing: 12m x 30m
building_a = unary_union([a_main, a_wing])  # Г-shape
building_b = box(15, 0, 65, 12)             # plain rectangle, facing A across the yard

buildings = [
    # "parts": the convex rectangles the 3D mesh is actually extruded from. A
    # single concave (Г-shaped) face confuses some DXF viewers' triangulation --
    # they draw a spurious diagonal straight across the notch, showing up as a
    # triangular sliver that shouldn't exist. Extruding the two convex source
    # rectangles separately (they share an internal wall, which just ends up
    # hidden inside the building) avoids that entirely.
    {"name": "A (Г-corpus)", "poly": building_a, "parts": [a_main, a_wing], "levels": 12.0},
    {"name": "B", "poly": building_b, "parts": [building_b], "levels": 8.0},
]
for b in buildings:
    b["height"] = b["levels"] * 3.0
    print(f"Building {b['name']}: footprint {b['poly'].area:.0f} m2, height {b['height']:.1f} m ({b['levels']:.0f} floors)")

all_buildings = unary_union([b["poly"] for b in buildings])

# --- Territory: rectangle around both buildings with room to spare ---
def oriented_rectangle(geom, margin):
    mrr = geom.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)[:4]
    edge = np.array(coords[1]) - np.array(coords[0])
    angle = math.degrees(math.atan2(edge[1], edge[0]))
    center = geom.centroid
    aligned = rotate(geom, -angle, origin=center)
    minx, miny, maxx, maxy = aligned.bounds
    rect_aligned = box(minx - margin, miny - margin, maxx + margin, maxy + margin)
    return rotate(rect_aligned, angle, origin=center), angle, center

territory, territory_angle, territory_center = oriented_rectangle(all_buildings, 30)
print(f"Territory area: {territory.area:.0f} m2")

# --- Parking: a modest rectangle along the east edge, clear of both buildings ---
open_ground = territory.difference(all_buildings.buffer(3))
tminx, tminy, tmaxx, tmaxy = territory.bounds
park_len, park_depth = 60.0, 16.0
park_cy = (building_a.centroid.y + building_b.centroid.y) / 2
parking_area = box(tmaxx - park_depth, park_cy - park_len / 2, tmaxx, park_cy + park_len / 2)
if parking_area.difference(open_ground).area > 0.5:
    for length in (50, 40, 32, 24):
        cand = box(tmaxx - park_depth, park_cy - length / 2, tmaxx, park_cy + length / 2)
        if cand.difference(open_ground).area < 0.5:
            parking_area = cand
            break
print(f"Parking area: {parking_area.area:.0f} m2")

stall_lines = []
mrr = parking_area.minimum_rotated_rectangle
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

# --- Entrances: on the wall of each building that actually FACES the other one
# -- scored by how well the wall's outward normal points toward the focus, not
# by raw nearest-point distance (on a Г-shaped building, an inner notch wall can
# be numerically closer to the focus while actually facing sideways into the
# notch instead of out into the yard) -- spaced by wall length, each with a canopy.
def true_outward(poly, mid, normal_candidate, eps=0.5):
    # A distance-to-centroid check breaks on a concave (Г-shaped) polygon: the
    # centroid of an L-shape can sit outside the polygon entirely (in the
    # notch), which flips the "outward" answer on the walls near that notch.
    # Testing containment of a point just off the wall is correct for any
    # simple polygon, convex or not.
    test_pt = Point(mid[0] + normal_candidate[0] * eps, mid[1] + normal_candidate[1] * eps)
    return -normal_candidate if poly.contains(test_pt) else normal_candidate

def pick_entrances(poly, focus_pt, min_len=15.0):
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
    enormal = np.array([-edir[1], edir[0]])
    mid = (e0 + e1) / 2
    enormal = true_outward(poly, mid, enormal)
    fracs = (0.2, 0.5, 0.8) if elen > 40 else (0.3, 0.7)
    ents = []
    for frac in fracs:
        pos = e0 + edir * (elen * frac)
        ents.append({"point": Point(pos[0], pos[1]), "normal": enormal, "dir": edir})
    return ents, best_i

buildings[0]["entrances"], buildings[0]["entrance_wall_idx"] = pick_entrances(building_a, (building_b.centroid.x, building_b.centroid.y))
buildings[1]["entrances"], buildings[1]["entrance_wall_idx"] = pick_entrances(building_b, (building_a.centroid.x, building_a.centroid.y))

# --- Footpaths: orthogonal routing from every entrance to the parking lot,
# avoiding both buildings ---
PATH_WIDTH = 1.8
CLEARANCE = 5.0
ang = math.radians(territory_angle)
u_axis = np.array([math.cos(ang), math.sin(ang)])
v_axis = np.array([-math.sin(ang), math.cos(ang)])
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

# --- Utilities: deliberately scattered -- each type enters from a different
# territory edge and crosses the open yard to reach BOTH buildings, instead of
# being bundled into one corridor along a single side. ---
# Power cable enters near the parking lot's edge, matching where the transformer
# substation actually sits (a cable run from a transformer somewhere else in the
# territory wouldn't make sense).
cable_entry = (tmaxx, park_cy)

UNDERGROUND = [
    # (layer, depth_z, color, entry_point)
    ("WATER_SUPPLY_B1", -1.6, 5, (tminx, (building_a.centroid.y + building_b.centroid.y) / 2)),
    ("SEWER_K1", -2.2, 30, ((building_a.centroid.x + 15) / 2, tminy)),
    ("HEATING_T1", -1.3, 1, (20, tmaxy)),
    ("POWER_CABLE", -0.7, 253, cable_entry),
]
GAS_ENTRY = (tminx, 60)

utility_lines = []
for layer, depth_z, color, entry in UNDERGROUND:
    utility_lines.append((layer, color, [(entry[0], entry[1], depth_z), (entry[0], entry[1], -0.3)]))
    for b in buildings:
        bp, _ = nearest_points(b["poly"].exterior, Point(entry))
        utility_lines.append((layer, color, [(entry[0], entry[1], depth_z), (bp.x, bp.y, depth_z), (bp.x, bp.y, -0.3)]))

GAS_LAYER, GAS_COLOR, GAS_Z = "GAS_PIPE", 2, 0.6
for b in buildings:
    bp, _ = nearest_points(b["poly"].exterior, Point(GAS_ENTRY))
    utility_lines.append((GAS_LAYER, GAS_COLOR, [(GAS_ENTRY[0], GAS_ENTRY[1], GAS_Z), (bp.x, bp.y, GAS_Z), (bp.x, bp.y, b["height"] - 1.0)]))

# --- Transformer substation: moved next to the parking lot (outside the
# territory, just beyond its east edge) -- easy vehicle access, NOT inside the
# courtyard, and right where the power cable actually comes in from. ---
transformer_center = (tmaxx + 8, park_cy)
TRANSFORMER_W, TRANSFORMER_D, TRANSFORMER_H = 4.0, 3.0, 2.8
transformer_poly = box(
    transformer_center[0] - TRANSFORMER_W / 2, transformer_center[1] - TRANSFORMER_D / 2,
    transformer_center[0] + TRANSFORMER_W / 2, transformer_center[1] + TRANSFORMER_D / 2,
)
# connect the cable's trunk entry to the substation itself
utility_lines.append(("POWER_CABLE", 253, [(cable_entry[0], cable_entry[1], -0.7), (transformer_center[0], transformer_center[1], -0.7), (transformer_center[0], transformer_center[1], 0.1)]))

# --- Small playground, inside the yard, clear of buildings/parking ---
playground_area = box(28, 33, 42, 45)
assert playground_area.difference(all_buildings.union(parking_area)).area == playground_area.area, "playground overlaps a building or the parking lot"

# --- Lamps: parking perimeter, every entrance, every footpath bend ---
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

add_boundary_lamps(parking_area, 1.5, 12)
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
grass_area = territory.difference(unary_union([all_buildings, parking_area, path_area, playground_area]))

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
doc.layers.add("TRANSFORMER_SUBSTATION", color=9)
doc.layers.add("PLAYGROUND", color=43)

# territory is a rectangle here but not necessarily axis-aligned; recenter using its bounds
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
add_filled_polygon(playground_area, "PLAYGROUND", 43)

for (sp0, sp1) in stall_lines:
    msp.add_line(shift([sp0])[0], shift([sp1])[0], dxfattribs={"layer": "PARKING_MARKINGS"})

# Playground equipment placeholders (simple geometric symbols)
pg_x, pg_y = shift([(30.5, 35.5)])[0]
msp.add_lwpolyline(shift([(30, 35), (33, 35), (33, 38), (30, 38)]), close=True, dxfattribs={"layer": "PLAYGROUND"})  # sandbox
bx, by = shift([(38, 41)])[0]
msp.add_line((bx, by, 0), (bx, by, 1.4), dxfattribs={"layer": "PLAYGROUND"})
msp.add_line((bx + 2.2, by, 0), (bx + 2.2, by, 1.4), dxfattribs={"layer": "PLAYGROUND"})
msp.add_line((bx, by, 1.4), (bx + 2.2, by, 1.4), dxfattribs={"layer": "PLAYGROUND"})  # swing frame
msp.add_lwpolyline(shift([(38, 33), (40.5, 33), (40.5, 33.6), (38, 33.6)]), close=True, dxfattribs={"layer": "PLAYGROUND"})  # bench

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

# Transformer substation: small extruded box, outside the yard
t_ext = shift(list(transformer_poly.exterior.coords))
msp.add_lwpolyline(t_ext, close=True, dxfattribs={"layer": "TRANSFORMER_SUBSTATION"})
tn = len(t_ext) - 1
t_base = t_ext[:tn]
t_mesh = msp.add_mesh(dxfattribs={"layer": "TRANSFORMER_SUBSTATION"})
with t_mesh.edit_data() as md:
    verts = [(x, y, 0.0) for x, y in t_base] + [(x, y, TRANSFORMER_H) for x, y in t_base]
    md.vertices = verts
    faces = [list(range(tn)), list(range(2 * tn - 1, tn - 1, -1))]
    for i in range(tn):
        j = (i + 1) % tn
        faces.append([i, j, tn + j, tn + i])
    md.faces = faces
tcx, tcy = transformer_center[0] - ox, transformer_center[1] - oy
msp.add_text("TP", dxfattribs={"layer": "TRANSFORMER_SUBSTATION", "height": 1.5, "insert": (tcx, tcy)})

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
    entrance_ts_by_edge = {}
    for i in range(n):
        w0, w1 = np.array(base[i]), np.array(base[(i + 1) % n])
        wall_vec = w1 - w0
        wall_len = np.linalg.norm(wall_vec)
        if wall_len < 15.0:
            continue
        wall_dir = wall_vec / wall_len
        wall_normal = np.array([-wall_dir[1], wall_dir[0]])
        wmid = (w0 + w1) / 2
        wmid_world = (wmid[0] + ox, wmid[1] + oy)
        wall_normal = true_outward(poly, wmid_world, wall_normal)

        entrance_ts = []
        if i == b["entrance_wall_idx"]:
            ewall_len = wall_len
            fracs = (0.2, 0.5, 0.8) if ewall_len > 40 else (0.3, 0.7)
            entrance_ts = [ewall_len * f for f in fracs]

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

OUT = "../03_lshape_vs_rect.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Grass/lawn area: {grass_area.area:.0f} m2")
print(f"Entrances: {sum(len(b['entrances']) for b in buildings)} (with canopies)")
print(f"Footpaths: {len(paths)}, total {path_area.area:.0f} m2")
print(f"Street lamps: {len(lamp_points)}")
print(f"Underground+gas utility runs: {len(utility_lines)} (5 types, each entering from a different edge)")
print(f"Transformer substation at {transformer_center}, outside the yard")
print(f"Playground area: {playground_area.area:.0f} m2")
