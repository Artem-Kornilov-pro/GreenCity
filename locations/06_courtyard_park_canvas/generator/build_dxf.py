# Location 06: "blank canvas" for a future park-design algorithm.
#
# 3 buildings form three sides of a square (a "П"/U shape, open to the north):
# a connecting building on the south, and two long arms on the west and east.
# All entrances face INWARD, into the large open courtyard they enclose.
#
# Unlike locations 02-05, the courtyard interior is deliberately left EMPTY --
# no footpaths, no benches, no trees, no playground. That empty space (with
# real constraints: territory boundary, building setbacks, underground
# utilities crossing it) is exactly what a not-yet-built park-design algorithm
# should later fill in with its own path network and planting plan. Everything
# OUTSIDE the courtyard (parking, entrances, utilities, windows) is filled in
# normally, same as the other locations -- only the courtyard itself is bare.
#
# Fully synthetic (no OSM fetch) -- an exact, reproducible test case.
# Run from this directory: python3 build_dxf.py
# Output is written one level up, as ../06_courtyard_park_canvas.dxf

import math
import numpy as np
from shapely.geometry import Polygon, Point, box, LineString
from shapely.ops import unary_union, nearest_points
import ezdxf

# --- 3 buildings forming a "П" shape open to the north ---------------------
D = 14.0                  # arm thickness (depth of each building)
COURTYARD_W = 70.0        # open courtyard interior width (between the two arms)
COURTYARD_D = 80.0        # open courtyard interior depth (south wall to open north edge)

south_poly = box(0.0, 0.0, 2 * D + COURTYARD_W, D)                       # connecting building
west_poly = box(0.0, D, D, D + COURTYARD_D)                              # west arm
east_poly = box(D + COURTYARD_W, D, 2 * D + COURTYARD_W, D + COURTYARD_D)  # east arm

buildings = [
    {"name": "Дом Ю (south)", "poly": south_poly, "parts": [south_poly], "levels": 8.0, "height": 8.0 * 3.0},
    {"name": "Дом З (west)", "poly": west_poly, "parts": [west_poly], "levels": 12.0, "height": 12.0 * 3.0},
    {"name": "Дом В (east)", "poly": east_poly, "parts": [east_poly], "levels": 10.0, "height": 10.0 * 3.0},
]
all_buildings = unary_union([b["poly"] for b in buildings])

courtyard = box(D, D, D + COURTYARD_W, D + COURTYARD_D)
print(f"Courtyard (blank canvas): {COURTYARD_W:.0f}x{COURTYARD_D:.0f}m = {courtyard.area:.0f} m2, open to the north")

# --- Territory: buildings + courtyard + a parking strip on the open (north) side ---
bminx, bminy, bmaxx, bmaxy = all_buildings.bounds
PARKING_GAP = 8.0    # street/access gap between the courtyard opening and parking
PARKING_DEPTH = 20.0
parking_area = box(bminx + 6, bmaxy + PARKING_GAP, bmaxx - 6, bmaxy + PARKING_GAP + PARKING_DEPTH)
tminx, tminy, tmaxx, tmaxy = bminx - 10, bminy - 10, bmaxx + 10, parking_area.bounds[3] + 10
territory = box(tminx, tminy, tmaxx, tmaxy)
territory_center = territory.centroid
print(f"Territory area: {territory.area:.0f} m2")

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

# --- Entrances: on the wall facing the courtyard centre (robust outward test,
# same as locations 03/04 -- handles any concave shape, though none here) ---
def true_outward(poly, mid, normal_candidate, eps=0.5):
    test_pt = Point(mid[0] + normal_candidate[0] * eps, mid[1] + normal_candidate[1] * eps)
    return -normal_candidate if poly.contains(test_pt) else normal_candidate

def pick_entrances(poly, focus_pt, min_len=10.0, fracs=(0.25, 0.5, 0.75)):
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

courtyard_focus = (courtyard.centroid.x, courtyard.centroid.y)
for b in buildings:
    b["entrances"], b["entrance_wall_idx"] = pick_entrances(b["poly"], courtyard_focus)

# --- No footpaths inside the courtyard -- that network is exactly what the
# future park-design algorithm is meant to invent. The only circulation drawn
# here is a short stub from each entrance out into the courtyard (so the door
# doesn't open onto a hard property line), never connected into a network. ---
ENTRANCE_STUB = 3.0

# --- Utilities: underground trunks run north-south through the courtyard
# (from the parking/street side down to the south building) plus a gas riser,
# each branching to whichever building is closest -- realistic, not
# deliberately adversarial like location 03/04. This is the real constraint
# data the future algorithm has to route its paths and plantings around.
# (No sewer or power cable trunk -- both previously cut straight through the
# middle of the canvas, one dead-centre and one diagonally; too obstructive
# for a space meant to stay open. Water/heating stay, offset toward the
# west/east arms rather than through the middle.) ---
UNDERGROUND = [
    ("WATER_SUPPLY_B1", -1.6, 5, LineString([(D + COURTYARD_W * 0.22, tmaxy - 12), (D + COURTYARD_W * 0.22, D + 1)])),
    ("HEATING_T1", -1.3, 1, LineString([(D + COURTYARD_W * 0.78, tmaxy - 12), (D + COURTYARD_W * 0.78, D + 1)])),
]

utility_lines = []
for layer, depth_z, color, trunk in UNDERGROUND:
    t0, t1 = list(trunk.coords)
    utility_lines.append((layer, color, [(*t0, depth_z), (*t1, depth_z)]))
    for b in buildings:
        bp, tp = nearest_points(b["poly"].exterior, trunk)
        utility_lines.append((layer, color, [(tp.x, tp.y, depth_z), (bp.x, bp.y, depth_z), (bp.x, bp.y, -0.3)]))

GAS_LAYER, GAS_COLOR, GAS_Z = "GAS_PIPE", 2, 0.6
gas_trunk = LineString([(D + COURTYARD_W * 0.9, tmaxy - 12), (D + COURTYARD_W * 0.9, D + 1)])
gt0, gt1 = list(gas_trunk.coords)
utility_lines.append((GAS_LAYER, GAS_COLOR, [(*gt0, GAS_Z), (*gt1, GAS_Z)]))
for b in buildings:
    bp, tp = nearest_points(b["poly"].exterior, gas_trunk)
    utility_lines.append((GAS_LAYER, GAS_COLOR, [(tp.x, tp.y, GAS_Z), (bp.x, bp.y, GAS_Z), (bp.x, bp.y, b["height"] - 1.0)]))

# --- Lamps: entrances + parking perimeter only -- NOT scattered through the
# courtyard, its lighting design is part of what the future algorithm decides ---
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
            lamp_points.append((px, py))
            target += step
        acc += seg_len

add_boundary_lamps(parking_area, 1.5, 12)
for b in buildings:
    for ent in b["entrances"]:
        lp = np.array([ent["point"].x, ent["point"].y]) + ent["normal"] * 1.5
        lamp_points.append(tuple(lp))

# Grass: the whole courtyard canvas + any other leftover ground in the territory
grass_area = territory.difference(unary_union([all_buildings, parking_area]))

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
add_filled_polygon(parking_area, "PARKING", 253)

# Entrance stubs: a short line straight out from the door, on the PATHS layer,
# but never connected onward -- deliberately not a network (see note above).
for b in buildings:
    for ent in b["entrances"]:
        p0 = (ent["point"].x, ent["point"].y)
        p1 = (p0[0] + ent["normal"][0] * ENTRANCE_STUB, p0[1] + ent["normal"][1] * ENTRANCE_STUB)
        msp.add_lwpolyline(shift([p0, p1]), dxfattribs={"layer": "PATHS"})

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

        entrance_ts = [wall_len * f for f in (0.25, 0.5, 0.75)] if i == b["entrance_wall_idx"] else []

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

OUT = "../06_courtyard_park_canvas.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Buildings: {len(buildings)} (П-shape, open north)")
print(f"Entrances: {sum(len(b['entrances']) for b in buildings)} (with canopies, facing courtyard)")
print(f"Grass/canvas area: {grass_area.area:.0f} m2 (of {territory.area:.0f} m2 total) -- NO paths/trees/benches placed inside")
print(f"Street lamps: {len(lamp_points)} (entrances + parking only)")
print(f"Underground+gas utility runs: {len(utility_lines)} (3 types x 3 buildings + 3 trunks)")
