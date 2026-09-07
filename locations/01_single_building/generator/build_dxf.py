# Location 01 of 5 test DXF locations (simplest -- ordered by increasing complexity).
# One real long rectangular apartment building (OSM, Moscow/Konkovo, addr 30/43,
# 218m footprint, raised to 13 floors) with 4 entrances (each with a canopy),
# footpaths from each entrance to the parking lot, a sizeable rectangular parking
# lot, underground utilities (water/sewer/heating/cable), a sparse/realistic
# window grid, and lamps at the parking perimeter, each entrance, and each
# footpath bend. No gas riser in this simple location -- that's introduced later.
#
# Run from this directory: python3 build_dxf.py
# Needs: buildings_raw.json, extra_raw.json (cached Overpass API responses, same
# Konkovo bbox used for location 02, kept alongside this script so the location
# can be rebuilt without re-fetching OSM data).
# Output is written one level up, as ../01_single_building.dxf

import json
import math
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, Point, box, LineString
from shapely.ops import unary_union, nearest_points
from shapely.affinity import rotate
import ezdxf

BUILDING_WAY_ID = 39129708  # addr 30/43, apartments, 5 floors, ~218m x 13m

transformer = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True)

with open("buildings_raw.json") as f:
    data = json.load(f)

nodes, ways = {}, {}
for el in data["elements"]:
    if el["type"] == "node":
        nodes[el["id"]] = (el["lon"], el["lat"])
    elif el["type"] == "way":
        ways[el["id"]] = el

way = ways[BUILDING_WAY_ID]
tags = way.get("tags", {})
coords_ll = [nodes[n] for n in way["nodes"] if n in nodes]
coords_m = [transformer.transform(lon, lat) for lon, lat in coords_ll]
if coords_m[0] != coords_m[-1]:
    coords_m.append(coords_m[0])
building_poly = Polygon(coords_m)
levels = 13.0  # raised from the real 5 floors, per request
height = levels * 3.0
name = tags.get("addr:housenumber", "bld")
print(f"Building {name}: footprint {building_poly.area:.0f} m2, height {height:.1f} m ({levels:.0f} floors)")

# Territory: an oriented rectangle around the building, generously margined so a
# sizeable parking lot fits alongside it.
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

territory, territory_angle, territory_center = oriented_rectangle(building_poly, 45)
print(f"Territory area: {territory.area:.0f} m2")

# --- Real parking nearby (OSM), used only to anchor which side the lot goes on ---
with open("extra_raw.json") as f:
    extra_data = json.load(f)

enodes, eways = {}, {}
for el in extra_data["elements"]:
    if el["type"] == "node":
        enodes[el["id"]] = (el["lon"], el["lat"])
    elif el["type"] == "way":
        eways[el["id"]] = el

real_parking_polys = []
for w in eways.values():
    t = w.get("tags", {})
    if t.get("amenity") not in ("parking", "parking_space"):
        continue
    cl = [enodes[n] for n in w["nodes"] if n in enodes]
    if len(cl) < 3:
        continue
    cm = [transformer.transform(lon, lat) for lon, lat in cl]
    if cm[0] != cm[-1]:
        cm.append(cm[0])
    p = Polygon(cm)
    if p.is_valid and p.distance(building_poly) < 120:
        real_parking_polys.append(p)

anchor = unary_union(real_parking_polys).centroid if real_parking_polys else territory_center
was_real = bool(real_parking_polys)

# --- Parking lot: a sizeable rectangle flush along the territory edge closest to
# the real OSM parking, running most of the building's length (not a small lot) ---
open_ground = territory.difference(building_poly.buffer(3))
corners = list(territory.exterior.coords)[:4]
edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
c0, c1 = min(edges, key=lambda e: LineString(e).distance(anchor))
evec = np.array(c1) - np.array(c0)
edge_len = np.linalg.norm(evec)
edir = evec / edge_len
enormal = np.array([-edir[1], edir[0]])
mid = (np.array(c0) + np.array(c1)) / 2
if math.dist(tuple(mid + enormal), (territory_center.x, territory_center.y)) > \
   math.dist(tuple(mid - enormal), (territory_center.x, territory_center.y)):
    enormal = -enormal  # inward

park_length = min(180.0, edge_len - 30.0)
park_depth = 18.0
p_start = np.array(mid) - edir * (park_length / 2)
p_end = np.array(mid) + edir * (park_length / 2)
parking_area = Polygon([
    tuple(p_start), tuple(p_end),
    tuple(p_end + enormal * park_depth), tuple(p_start + enormal * park_depth),
])
if parking_area.difference(open_ground).area > 0.5:
    # shrink until it fits cleanly inside the open ground
    for length in (160, 140, 120, 100, 80, 60):
        p_start = np.array(mid) - edir * (length / 2)
        p_end = np.array(mid) + edir * (length / 2)
        cand = Polygon([
            tuple(p_start), tuple(p_end),
            tuple(p_end + enormal * park_depth), tuple(p_start + enormal * park_depth),
        ])
        if cand.difference(open_ground).area < 0.5:
            parking_area = cand
            break

print(f"Parking area: {parking_area.area:.0f} m2, anchored on {'real OSM' if was_real else 'synthesized'} location")

# Stall divider lines along the lot's long axis
stall_lines = []
mrr = parking_area.minimum_rotated_rectangle
mc = list(mrr.exterior.coords)[:4]
p_edges = [(mc[i], mc[(i + 1) % 4]) for i in range(4)]
p_edges.sort(key=lambda e: -math.dist(e[0], e[1]))
(a0, a1), (b0, b1) = p_edges[0], p_edges[1]
n_stalls = max(1, int(math.dist(a0, a1) // 2.5))
dx, dy = b1[0] - b0[0], b1[1] - b0[1]
dl = math.hypot(dx, dy) or 1
depth = min(dl, 5.5)
dx, dy = dx / dl * depth, dy / dl * depth
for i in range(1, n_stalls):
    t = i / n_stalls
    sx = a0[0] + (a1[0] - a0[0]) * t
    sy = a0[1] + (a1[1] - a0[1]) * t
    stall_lines.append(((sx, sy), (sx + dx, sy + dy)))

# --- 4 entrances, evenly spaced along the long facade facing the parking lot ---
ext_pts = list(building_poly.exterior.coords)
n_bpts = len(ext_pts) - 1
best_i, best_d = None, None
for i in range(n_bpts):
    p0, p1 = ext_pts[i], ext_pts[i + 1]
    fmid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    d = math.dist(fmid, (parking_area.centroid.x, parking_area.centroid.y))
    if best_d is None or d < best_d:
        best_d, best_i = d, i

e0, e1 = np.array(ext_pts[best_i]), np.array(ext_pts[best_i + 1])
fvec = e1 - e0
flen = np.linalg.norm(fvec)
fdir = fvec / flen
fnormal = np.array([-fdir[1], fdir[0]])
fmid = (e0 + e1) / 2
bc = np.array([building_poly.centroid.x, building_poly.centroid.y])
if math.dist(tuple(fmid + fnormal * 0.1), tuple(bc)) < math.dist(tuple(fmid - fnormal * 0.1), tuple(bc)):
    fnormal = -fnormal

entrance_wall_idx = best_i
entrances = []
for frac in (0.15, 0.38, 0.62, 0.85):
    pos = e0 + fdir * (flen * frac)
    entrances.append({"point": Point(pos[0], pos[1]), "normal": fnormal, "dir": fdir})

# --- Footpaths (тропинки): orthogonal (right-angle) routing in the territory's own
# axis frame, from each entrance to the parking lot -- not a diagonal shortest-
# distance line that could cut across the building. ---
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

building_buffered = building_poly.buffer(0.5)
paths = []
path_bend_points = []
for ent in entrances:
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
        if not seg.intersects(building_buffered):
            chosen_bend = bend
            break
    if chosen_bend is None:
        chosen_bend = min(
            candidates,
            key=lambda bend: LineString([exit_pt, bend, dest]).intersection(building_buffered).length,
        )

    route = [door, exit_pt, chosen_bend, dest]
    path_bend_points.append(exit_pt)
    path_bend_points.append(chosen_bend)
    path_poly = LineString(route).buffer(PATH_WIDTH / 2, cap_style=2, join_style=2)
    paths.append(path_poly.difference(building_poly.buffer(0.2)))

path_area = unary_union(paths) if paths else Polygon()

# --- Underground utilities: water, sewer, heating, power cable -- trunk along the
# territory edge farthest from the building (the "street" side), branch to the
# building at each entrance-facing wall. No gas riser in this simple location. ---
street_edge = max(edges, key=lambda e: LineString(e).distance(building_poly.centroid))
(s0, s1) = street_edge
svec = np.array(s1) - np.array(s0)
slen = np.linalg.norm(svec)
sdir = svec / slen
snormal = np.array([-sdir[1], sdir[0]])
if math.dist(tuple(np.array(s0) + snormal * 5), (building_poly.centroid.x, building_poly.centroid.y)) > \
   math.dist(tuple(np.array(s0) - snormal * 5), (building_poly.centroid.x, building_poly.centroid.y)):
    snormal = -snormal

sinset0 = np.array(s0) + sdir * 6
sinset1 = np.array(s1) - sdir * 6

UNDERGROUND = [
    ("WATER_SUPPLY_B1", -1.6, 3.0, 5),
    ("SEWER_K1", -2.2, 4.5, 30),
    ("HEATING_T1", -1.3, 6.0, 1),
    ("POWER_CABLE", -0.7, 7.5, 253),
]

utility_lines = []
for layer, depth_z, offset, color in UNDERGROUND:
    t0 = sinset0 + snormal * offset
    t1 = sinset1 + snormal * offset
    trunk = LineString([tuple(t0), tuple(t1)])
    utility_lines.append((layer, color, [(*t0, depth_z), (*t1, depth_z)]))
    bp, tp = nearest_points(building_poly.exterior, trunk)
    utility_lines.append((layer, color, [(tp.x, tp.y, depth_z), (bp.x, bp.y, depth_z), (bp.x, bp.y, -0.3)]))

# --- Lamps: around the parking lot perimeter + at each entrance ---
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
            if not building_poly.buffer(1.5).contains(Point(px, py)):
                lamp_points.append((px, py))
            target += step
        acc += seg_len

add_boundary_lamps(parking_area, 1.5, 12)
for ent in entrances:
    lp = np.array([ent["point"].x, ent["point"].y]) + ent["normal"] * 1.5
    lamp_points.append(tuple(lp))

seen = set()
for (px, py) in path_bend_points:
    key = (round(px, 1), round(py, 1))
    if key in seen:
        continue
    seen.add(key)
    if not building_poly.buffer(1.5).contains(Point(px, py)):
        lamp_points.append((px, py))

# Grass: open ground not covered by the building, the parking lot, or the footpaths
grass_area = territory.difference(unary_union([building_poly, parking_area, path_area]))

# --- Build DXF ---
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
    a = shift([sp0])[0]
    b = shift([sp1])[0]
    msp.add_line(a, b, dxfattribs={"layer": "PARKING_MARKINGS"})

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

# Building: footprint + extruded 3D box
ext = shift(list(building_poly.exterior.coords))
msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": "BUILDING_FOOTPRINT"})
n = len(ext) - 1
base = ext[:n]
mesh = msp.add_mesh(dxfattribs={"layer": "BUILDING_3D"})
with mesh.edit_data() as mesh_data:
    verts = [(x, y, 0.0) for x, y in base] + [(x, y, height) for x, y in base]
    mesh_data.vertices = verts
    faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    mesh_data.faces = faces

# Windows: sparse/realistic -- only on the two long facades (the narrow 13m gable
# ends are skipped, matching real long panel buildings which have little or no
# glazing there), moderate pitch so it doesn't read as a dense grid.
bcx, bcy = building_poly.centroid.x - ox, building_poly.centroid.y - oy
floor_h = height / levels
win_w, win_h, sill, margin, spacing = 1.3, 1.4, 1.0, 2.0, 6.0
for i in range(n):
    w0, w1 = np.array(base[i]), np.array(base[(i + 1) % n])
    wall_vec = w1 - w0
    wall_len = np.linalg.norm(wall_vec)
    if wall_len < 15.0:  # skips the short 13m gable ends
        continue
    wall_dir = wall_vec / wall_len
    wall_normal = np.array([-wall_dir[1], wall_dir[0]])
    wmid = (w0 + w1) / 2
    if math.dist(tuple(wmid + wall_normal * 0.1), (bcx, bcy)) < math.dist(tuple(wmid - wall_normal * 0.1), (bcx, bcy)):
        wall_normal = -wall_normal

    is_entrance_wall = i == entrance_wall_idx
    entrance_ts = [flen * f for f in (0.15, 0.38, 0.62, 0.85)] if is_entrance_wall else []

    usable = wall_len - 2 * margin
    n_win = max(1, int(usable // spacing)) if usable > spacing else 0
    for lvl in range(int(levels)):
        z0, z1 = lvl * floor_h + sill, lvl * floor_h + sill + win_h
        for wi in range(n_win):
            t = margin + (usable / n_win) * (wi + 0.5)
            if lvl == 0 and any(abs(t - et) < 2.4 for et in entrance_ts):
                continue
            c = w0 + wall_dir * t + wall_normal * 0.05
            p_left = c - wall_dir * win_w / 2
            p_right = c + wall_dir * win_w / 2
            msp.add_3dface(
                [(*p_left, z0), (*p_right, z0), (*p_right, z1), (*p_left, z1)],
                dxfattribs={"layer": "WINDOWS"},
            )

# Entrances: door marker + canopy (козырёк) at first-floor height
canopy_h = min(floor_h - 0.3, 3.2)
canopy_depth, canopy_width = 1.4, 2.6
for ent in entrances:
    dx, dy = ent["point"].x - ox, ent["point"].y - oy
    msp.add_circle(center=(dx, dy, 0.05), radius=0.5, dxfattribs={"layer": "ENTRANCES"})

    inner = np.array([dx, dy])
    outer = inner + ent["normal"] * canopy_depth
    p1 = inner - ent["dir"] * canopy_width / 2
    p2 = inner + ent["dir"] * canopy_width / 2
    p3 = outer + ent["dir"] * canopy_width / 2
    p4 = outer - ent["dir"] * canopy_width / 2
    msp.add_3dface(
        [(*p1, canopy_h), (*p2, canopy_h), (*p3, canopy_h), (*p4, canopy_h)],
        dxfattribs={"layer": "CANOPIES"},
    )
    msp.add_3dface(
        [(*p3, canopy_h), (*p4, canopy_h), (*p4, canopy_h - 0.25), (*p3, canopy_h - 0.25)],
        dxfattribs={"layer": "CANOPIES"},
    )

cx, cy = building_poly.centroid.x - ox, building_poly.centroid.y - oy
msp.add_text(f"{name} h={height:.1f}m", dxfattribs={"layer": "LABELS", "height": 2.5, "insert": (cx, cy)})

doc.set_modelspace_vport(height=(maxy - miny + 60), center=((maxx - minx) / 2, (maxy - miny) / 2))

OUT = "../01_single_building.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Grass/lawn area: {grass_area.area:.0f} m2")
print(f"Entrances: {len(entrances)} (with canopies)")
print(f"Footpaths: {len(paths)}, total {path_area.area:.0f} m2")
print(f"Street lamps: {len(lamp_points)}")
print(f"Underground utility runs: {len(utility_lines)}")
