# Location 02 of 5 test DXF locations (ordered by increasing complexity).
# Single courtyard: 3 real apartment buildings (OSM, Moscow/Konkovo) + territory,
# parking, footpaths, entrances/canopies, underground+gas utilities, windows.
# Run from this directory: python3 build_dxf.py
# Needs: buildings_raw.json, extra_raw.json (cached Overpass API responses, kept
# alongside this script so the location can be rebuilt without re-fetching OSM data).
# Output is written one level up, as ../02_courtyard_3buildings.dxf

import json
import math
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, MultiPolygon, Point, box, LineString
from shapely.ops import unary_union, nearest_points
from shapely.affinity import rotate, translate
import ezdxf
from ezdxf import zoom

RAW = "buildings_raw.json"

with open(RAW) as f:
    data = json.load(f)

nodes = {}
ways = {}
for el in data["elements"]:
    if el["type"] == "node":
        nodes[el["id"]] = (el["lon"], el["lat"])
    elif el["type"] == "way":
        ways[el["id"]] = el

# WGS84 -> local metric projection (UTM zone 37N covers Moscow)
transformer = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True)

buildings = []
for wid, way in ways.items():
    tags = way.get("tags", {})
    if "building" not in tags:
        continue
    coords_ll = [nodes[n] for n in way["nodes"] if n in nodes]
    if len(coords_ll) < 3:
        continue
    coords_m = [transformer.transform(lon, lat) for lon, lat in coords_ll]
    if coords_m[0] != coords_m[-1]:
        coords_m.append(coords_m[0])
    poly = Polygon(coords_m)
    if not poly.is_valid or poly.area < 20:
        continue

    levels = tags.get("building:levels")
    try:
        levels = float(levels)
    except (TypeError, ValueError):
        levels = 5.0  # typical Soviet-era panel building default
    height = tags.get("height")
    try:
        height = float(str(height).replace("m", "").strip())
    except (TypeError, ValueError):
        height = levels * 3.0

    buildings.append({
        "id": wid,
        "poly": poly,
        "height": height,
        "levels": levels,
        "btype": tags.get("building"),
        "addr": tags.get("addr:housenumber"),
        "name": tags.get("addr:housenumber") or tags.get("name") or f"bld_{wid}",
    })

print(f"Found {len(buildings)} valid building footprints")

# Restrict to genuine multi-storey apartment buildings with a real address for a realistic courtyard
res_buildings = [
    b for b in buildings
    if b["levels"] >= 4 and b["btype"] in ("apartments", "residential", "house") and b["addr"]
]
print(f"{len(res_buildings)} residential (>=4 floors) buildings")

# Find a triple of buildings that actually *encloses* a courtyard: search all
# combinations, score by how enclosing (triangle area) vs how compact (tight) they are.
import itertools

best = None
for combo in itertools.combinations(res_buildings, 3):
    centroids = [b["poly"].centroid for b in combo]
    pair_dists = [centroids[i].distance(centroids[j]) for i, j in ((0, 1), (0, 2), (1, 2))]
    avg_dist = sum(pair_dists) / 3
    if avg_dist > 130:  # too far apart to be one courtyard
        continue
    heights = [b["height"] for b in combo]
    if max(heights) / min(heights) > 1.5:  # keep a coherent, similar-height ensemble
        continue
    x = [p.x for p in centroids]
    y = [p.y for p in centroids]
    tri_area = abs(x[0] * (y[1] - y[2]) + x[1] * (y[2] - y[0]) + x[2] * (y[0] - y[1])) / 2
    if tri_area < 200:  # near-collinear, not an enclosing shape
        continue
    score = tri_area / (avg_dist ** 2)
    if best is None or score > best[0]:
        best = (score, avg_dist, list(combo))

score, spread, cluster = best
print(f"Selected cluster avg_dist={spread:.1f}m, score={score:.3f}, buildings={[b['name'] for b in cluster]}")

# Enforce a minimum 30m clearance between buildings (fire-safety separation): scale
# the triangle of building centroids outward from the cluster's own centroid until
# every pairwise gap clears the minimum, keeping their relative arrangement unchanged.
MIN_GAP = 30.0
ccx = np.mean([b["poly"].centroid.x for b in cluster])
ccy = np.mean([b["poly"].centroid.y for b in cluster])
sep_vecs = [np.array([b["poly"].centroid.x - ccx, b["poly"].centroid.y - ccy]) for b in cluster]

def min_gap_at(scale):
    polys = [translate(b["poly"], xoff=v[0] * (scale - 1), yoff=v[1] * (scale - 1)) for b, v in zip(cluster, sep_vecs)]
    gap = min(polys[i].distance(polys[j]) for i in range(3) for j in range(i + 1, 3))
    return gap, polys

gap0, _ = min_gap_at(1.0)
if gap0 < MIN_GAP:
    lo, hi = 1.0, 2.0
    g, _ = min_gap_at(hi)
    while g < MIN_GAP and hi < 30:
        hi *= 1.5
        g, _ = min_gap_at(hi)
    for _ in range(40):
        mid = (lo + hi) / 2
        g, _ = min_gap_at(mid)
        if g < MIN_GAP:
            lo = mid
        else:
            hi = mid
    final_gap, final_polys = min_gap_at(hi)
    for b, p in zip(cluster, final_polys):
        b["poly"] = p
    print(f"Buildings were {gap0:.1f}m apart at closest; spaced out to {final_gap:.1f}m (>= {MIN_GAP}m fire-safety minimum)")
else:
    print(f"Buildings already {gap0:.1f}m apart at closest (>= {MIN_GAP}m minimum, no adjustment needed)")

# Territory: a rectangle around the 3 buildings, oriented to match them and
# expanded outward to leave room for a bigger parking lot and access roads.
union = unary_union([b["poly"] for b in cluster])

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

territory, territory_angle, territory_center = oriented_rectangle(union, 40)

# --- Load real parking / grass from OSM, fall back to synthesized placement ---
with open("extra_raw.json") as f:
    extra_data = json.load(f)

extra_nodes = {}
extra_ways = {}
for el in extra_data["elements"]:
    if el["type"] == "node":
        extra_nodes[el["id"]] = (el["lon"], el["lat"])
    elif el["type"] == "way":
        extra_ways[el["id"]] = el

def way_to_poly(way):
    coords_ll = [extra_nodes[n] for n in way["nodes"] if n in extra_nodes]
    if len(coords_ll) < 3:
        return None
    coords_m = [transformer.transform(lon, lat) for lon, lat in coords_ll]
    if coords_m[0] != coords_m[-1]:
        coords_m.append(coords_m[0])
    poly = Polygon(coords_m)
    return poly if poly.is_valid and poly.area > 5 else None

parking_polys, grass_polys = [], []
for way in extra_ways.values():
    tags = way.get("tags", {})
    poly = way_to_poly(way)
    if poly is None or not poly.intersects(territory):
        continue
    clipped = poly.intersection(territory)
    if clipped.is_empty:
        continue
    if tags.get("amenity") in ("parking", "parking_space"):
        parking_polys.append(clipped)
    elif tags.get("landuse") == "grass" or tags.get("leisure") == "garden":
        grass_polys.append(clipped)

parking_area = unary_union(parking_polys) if parking_polys else Polygon()
# keep clearance from building walls
open_ground = territory.difference(union.buffer(3))

def find_rect(area_poly, length, width):
    """Brute-force search for a length x width rectangle fully inside area_poly."""
    if area_poly.is_empty or area_poly.area < length * width * 0.7:
        return None
    minx, miny, maxx, maxy = area_poly.bounds
    xs = np.linspace(minx, maxx, 10)
    ys = np.linspace(miny, maxy, 10)
    for angle in (0, 45, 90, 135):
        for x in xs:
            for y in ys:
                c = Point(x, y)
                if not area_poly.contains(c):
                    continue
                rect = rotate(box(x - length / 2, y - width / 2, x + length / 2, y + width / 2), angle, origin=c)
                if area_poly.contains(rect):
                    return rect
    return None

# Stretch the parking lot flush along one full territory edge -- corner to corner,
# on whichever side is closest to its original spot -- instead of leaving it
# floating as a disconnected island in the middle of the yard.
was_real = not parking_area.is_empty
if was_real:
    anchor = parking_area.centroid
else:
    parts = [open_ground] if isinstance(open_ground, Polygon) else list(open_ground.geoms)
    parts.sort(key=lambda g: g.area, reverse=True)
    anchor = parts[0].centroid if parts else union.centroid

corners = list(territory.exterior.coords)[:4]
edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
c0, c1 = min(edges, key=lambda e: LineString(e).distance(anchor))
evec = np.array(c1) - np.array(c0)
edir = evec / np.linalg.norm(evec)
enormal = np.array([-edir[1], edir[0]])
mid = (np.array(c0) + np.array(c1)) / 2
if math.dist(tuple(mid + enormal), (territory_center.x, territory_center.y)) > \
   math.dist(tuple(mid - enormal), (territory_center.x, territory_center.y)):
    enormal = -enormal  # point inward, toward the yard

bigger = None
for depth in (16, 14, 12, 10, 8):
    rect = Polygon([tuple(c0), tuple(c1), tuple(np.array(c1) + enormal * depth), tuple(np.array(c0) + enormal * depth)])
    # tolerant containment check: boolean ops on the territory rectangle can leave
    # sub-metre floating point noise right along its boundary, which makes a strict
    # .contains() fail even though the rectangle really does fit
    if rect.difference(open_ground).area < 0.5:
        bigger = rect
        break

if bigger is None:
    # full edge won't fit (unlikely) -- fall back to a freestanding rectangle
    parts = [open_ground] if isinstance(open_ground, Polygon) else list(open_ground.geoms)
    parts.sort(key=lambda g: g.area, reverse=True)
    for length, width in ((40, 12), (32, 11), (26, 10)):
        for part in parts[:3]:
            bigger = find_rect(part, length, width)
            if bigger:
                break
        if bigger:
            break

if bigger is not None:
    parking_area = bigger

# Parking stall divider lines (every 2.5m along the lot's long axis)
stall_lines = []
if not parking_area.is_empty:
    mrr = parking_area.minimum_rotated_rectangle
    mc = list(mrr.exterior.coords)[:4]
    edges = [(mc[i], mc[(i + 1) % 4]) for i in range(4)]
    edges.sort(key=lambda e: -math.dist(e[0], e[1]))
    long_edge = edges[0]      # the lot's long axis (row of stalls runs along this)
    short_edge = edges[1]     # stall depth direction
    (a0, a1) = long_edge
    length = math.dist(a0, a1)
    n_stalls = max(1, int(length // 2.5))
    (b0, b1) = short_edge
    dx, dy = b1[0] - b0[0], b1[1] - b0[1]
    dl = math.hypot(dx, dy) or 1
    depth = min(dl, 5.5)
    dx, dy = dx / dl * depth, dy / dl * depth
    for i in range(1, n_stalls):
        t = i / n_stalls
        sx = a0[0] + (a1[0] - a0[0]) * t
        sy = a0[1] + (a1[1] - a0[1]) * t
        stall_lines.append(((sx, sy), (sx + dx, sy + dy)))

# Two entrances (подъезды) per building, placed on the wall facing the courtyard
# centre (OSM has no entrance nodes for this courtyard, so the facade itself is the
# best stand-in for "which side residents actually enter from").
cluster_cx = np.mean([b["poly"].centroid.x for b in cluster])
cluster_cy = np.mean([b["poly"].centroid.y for b in cluster])

for b in cluster:
    ext_pts = list(b["poly"].exterior.coords)
    n_pts = len(ext_pts) - 1
    best_i, best_d = None, None
    for i in range(n_pts):
        p0, p1 = ext_pts[i], ext_pts[i + 1]
        mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        d = math.dist(mid, (cluster_cx, cluster_cy))
        if best_d is None or d < best_d:
            best_d, best_i = d, i

    e0, e1 = np.array(ext_pts[best_i]), np.array(ext_pts[best_i + 1])
    evec = e1 - e0
    elen = np.linalg.norm(evec)
    edir = evec / elen
    enormal = np.array([-edir[1], edir[0]])
    mid = (e0 + e1) / 2
    bc = np.array([b["poly"].centroid.x, b["poly"].centroid.y])
    if math.dist(tuple(mid + enormal * 0.1), tuple(bc)) < math.dist(tuple(mid - enormal * 0.1), tuple(bc)):
        enormal = -enormal

    fracs = (0.3, 0.7) if elen > 12 else (0.35, 0.65)
    b["entrance_wall_idx"] = best_i
    b["entrances"] = []
    for frac in fracs:
        pos = e0 + edir * (elen * frac)
        b["entrances"].append({"point": Point(pos[0], pos[1]), "t": elen * frac, "normal": enormal, "dir": edir})

# Footpaths (тропинки): orthogonal (right-angle) routing in the territory's own axis
# frame, not diagonal shortest-distance lines that can cut across -- or through --
# a building. Each path leaves its entrance perpendicular to the facade, then bends
# at 90 degrees toward the parking lot; whichever bend order clears the buildings is used.
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

buildings_buffered = union.buffer(0.5)

paths = []
path_bend_points = []
for b in cluster:
    for ent in b["entrances"]:
        door = (ent["point"].x, ent["point"].y)
        exit_pt = (door[0] + ent["normal"][0] * CLEARANCE, door[1] + ent["normal"][1] * CLEARANCE)
        target = parking_area if not parking_area.is_empty else territory.boundary
        _, dest_pt = nearest_points(Point(exit_pt), target)
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
            chosen_bend = min(
                candidates,
                key=lambda bend: LineString([exit_pt, bend, dest]).intersection(buildings_buffered).length,
            )

        route = [door, exit_pt, chosen_bend, dest]
        path_bend_points.append(exit_pt)
        path_bend_points.append(chosen_bend)
        path_poly = LineString(route).buffer(PATH_WIDTH / 2, cap_style=2, join_style=2)
        paths.append(path_poly.difference(union.buffer(0.2)))

path_area = unary_union(paths) if paths else Polygon()

# Grass: all open ground not covered by buildings, the parking lot, or the footpaths
occupied = unary_union([g for g in (union, parking_area, path_area) if not g.is_empty])
grass_area = territory.difference(occupied)
if grass_polys:
    grass_area = unary_union([grass_area] + grass_polys).difference(occupied)

# Lamps: at every entrance, along the footpaths (at each bend), and around the
# parking lot -- no perimeter lighting around the open territory boundary.
LAMP_HEIGHT = 4.0
lamp_points = []

for b in cluster:
    for ent in b["entrances"]:
        lp = np.array([ent["point"].x, ent["point"].y]) + ent["normal"] * 1.5
        lamp_points.append(tuple(lp))

seen = set()
for (px, py) in path_bend_points:
    key = (round(px, 1), round(py, 1))
    if key in seen:
        continue
    seen.add(key)
    if not union.buffer(1.5).contains(Point(px, py)):
        lamp_points.append((px, py))

def add_boundary_lamps(poly, inset, spacing):
    ring = poly.buffer(-inset) if inset else poly
    if ring.is_empty:
        return
    ring = ring if isinstance(ring, Polygon) else max(ring.geoms, key=lambda g: g.area)
    coords = list(ring.exterior.coords)
    total_len = sum(math.dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
    n = max(4, int(total_len // spacing))
    acc, idx = 0.0, 0
    step = total_len / n
    target = 0.0
    for i in range(len(coords) - 1):
        p0, p1 = coords[i], coords[i + 1]
        seg_len = math.dist(p0, p1)
        while target <= acc + seg_len:
            t = (target - acc) / seg_len if seg_len else 0
            px = p0[0] + (p1[0] - p0[0]) * t
            py = p0[1] + (p1[1] - p0[1]) * t
            pt = Point(px, py)
            if not union.buffer(1.5).contains(pt):
                lamp_points.append((px, py))
            target += step
        acc += seg_len

if not parking_area.is_empty:
    add_boundary_lamps(parking_area, 1.5, 12)

# --- Utility networks: underground water/sewer/heating/cable, above-ground gas ---
# OSM has no real utility data for this courtyard (checked: 0 pipeline/cable/utility
# elements via Overpass), so routes are synthesized following typical Russian courtyard
# practice: trunk lines run along the "street" side of the territory with a branch to
# each building; gas runs above ground with a riser up the facade, per SP 42.13330.2016.
rect_corners = list(territory.exterior.coords)[:4]
rect_edges = [(rect_corners[i], rect_corners[(i + 1) % 4]) for i in range(4)]
street_edge = max(rect_edges, key=lambda e: LineString(e).distance(union.centroid))
(s0, s1) = street_edge
edge_vec = np.array(s1) - np.array(s0)
edge_len = np.linalg.norm(edge_vec)
edge_dir = edge_vec / edge_len
normal = np.array([-edge_dir[1], edge_dir[0]])
if math.dist(tuple(np.array(s0) + normal * 5), (union.centroid.x, union.centroid.y)) > \
   math.dist(tuple(np.array(s0) - normal * 5), (union.centroid.x, union.centroid.y)):
    normal = -normal

inset0 = np.array(s0) + edge_dir * 6
inset1 = np.array(s1) - edge_dir * 6

# (layer, elevation z, offset from street edge, ACI color)
UNDERGROUND = [
    ("WATER_SUPPLY_B1", -1.6, 3.0, 5),
    ("SEWER_K1", -2.2, 4.5, 30),
    ("HEATING_T1", -1.3, 6.0, 1),
    ("POWER_CABLE", -0.7, 7.5, 253),
]

utility_lines = []  # (layer, color, [(x, y, z), ...])
for layer, depth, offset, color in UNDERGROUND:
    t0 = inset0 + normal * offset
    t1 = inset1 + normal * offset
    trunk = LineString([tuple(t0), tuple(t1)])
    utility_lines.append((layer, color, [(*t0, depth), (*t1, depth)]))
    for b in cluster:
        bp, tp = nearest_points(b["poly"].exterior, trunk)
        utility_lines.append((layer, color, [(tp.x, tp.y, depth), (bp.x, bp.y, depth), (bp.x, bp.y, -0.3)]))

GAS_LAYER, GAS_COLOR, GAS_Z = "GAS_PIPE", 2, 0.6
gt0 = inset0 + normal * 9.0
gt1 = inset1 + normal * 9.0
gas_trunk = LineString([tuple(gt0), tuple(gt1)])
utility_lines.append((GAS_LAYER, GAS_COLOR, [(*gt0, GAS_Z), (*gt1, GAS_Z)]))
for b in cluster:
    bp, tp = nearest_points(b["poly"].exterior, gas_trunk)
    utility_lines.append((GAS_LAYER, GAS_COLOR, [(tp.x, tp.y, GAS_Z), (bp.x, bp.y, GAS_Z), (bp.x, bp.y, b["height"] - 1.0)]))

# --- Build DXF ---
doc = ezdxf.new("R2010", setup=True)
doc.units = ezdxf.units.M
msp = doc.modelspace()

doc.layers.add("TERRITORY_BOUNDARY", color=8)
doc.layers.add("BUILDINGS_FOOTPRINT", color=1)
doc.layers.add("BUILDINGS_3D", color=3)
doc.layers.add("LABELS", color=7)
doc.layers.add("GRASS", color=84)
doc.layers.add("PARKING", color=253)
doc.layers.add("PARKING_MARKINGS", color=7)
doc.layers.add("PATHS", color=9)
doc.layers.add("CANOPIES", color=132)
doc.layers.add("LAMPS", color=2)
doc.layers.add("WATER_SUPPLY_B1", color=5)
doc.layers.add("SEWER_K1", color=30)
doc.layers.add("HEATING_T1", color=1)
doc.layers.add("POWER_CABLE", color=253)
doc.layers.add("GAS_PIPE", color=2)
doc.layers.add("WINDOWS", color=150)

# Recenter coordinates near origin for convenience
minx, miny, maxx, maxy = territory.bounds
ox, oy = minx - 10, miny - 10

def shift(coords):
    return [(x - ox, y - oy) for x, y in coords]

# Territory boundary (closed polyline)
territory_coords = shift(list(territory.exterior.coords))
msp.add_lwpolyline(territory_coords, close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})

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

# Grass covers the open ground first, then paths, then parking on top
add_filled_polygon(grass_area, "GRASS", 84)
add_filled_polygon(path_area, "PATHS", 9)
add_filled_polygon(parking_area, "PARKING", 253)

for (p0, p1) in stall_lines:
    a = shift([p0])[0]
    b = shift([p1])[0]
    msp.add_line(a, b, dxfattribs={"layer": "PARKING_MARKINGS"})

# Street lamps: pole + fixture, skipped where they'd land inside a building
for (x, y) in lamp_points:
    sx, sy = x - ox, y - oy
    msp.add_line((sx, sy, 0), (sx, sy, LAMP_HEIGHT), dxfattribs={"layer": "LAMPS"})
    msp.add_circle(center=(sx, sy, LAMP_HEIGHT), radius=0.35, dxfattribs={"layer": "LAMPS"})

# Utility lines: 2-point runs as LINE, branch/riser runs (3 points) as 3D polylines
for layer, color, pts in utility_lines:
    shifted = [(x - ox, y - oy, z) for x, y, z in pts]
    if len(shifted) == 2:
        msp.add_line(shifted[0], shifted[1], dxfattribs={"layer": layer, "color": color})
    else:
        msp.add_polyline3d(shifted, dxfattribs={"layer": layer, "color": color})

# Buildings: 2D footprint + extruded 3D solid (prism via mesh) + windows + label
for b in cluster:
    ext = shift(list(b["poly"].exterior.coords))
    msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": "BUILDINGS_FOOTPRINT"})

    h = b["height"]
    n = len(ext) - 1  # last point duplicates first
    base = ext[:n]

    mesh = msp.add_mesh(dxfattribs={"layer": "BUILDINGS_3D"})
    with mesh.edit_data() as mesh_data:
        verts = []
        for x, y in base:
            verts.append((x, y, 0.0))
        for x, y in base:
            verts.append((x, y, h))
        mesh_data.vertices = verts

        faces = []
        # bottom face
        faces.append(list(range(n)))
        # top face (reversed winding)
        faces.append(list(range(2 * n - 1, n - 1, -1)))
        # side walls
        for i in range(n):
            j = (i + 1) % n
            faces.append([i, j, n + j, n + i])
        mesh_data.faces = faces

    # Windows: a sparser, more realistic grid -- one window per room-width module,
    # skipped near ground-floor entrances where the door sits instead.
    centroid = b["poly"].centroid
    cx, cy = centroid.x - ox, centroid.y - oy
    floor_h = h / b["levels"]
    win_w, win_h, sill, margin, spacing = 1.3, 1.4, 1.0, 1.8, 4.2
    for i in range(n):
        w0, w1 = np.array(base[i]), np.array(base[(i + 1) % n])
        wall_vec = w1 - w0
        wall_len = np.linalg.norm(wall_vec)
        if wall_len < 4.0:
            continue
        wall_dir = wall_vec / wall_len
        wall_normal = np.array([-wall_dir[1], wall_dir[0]])
        mid = (w0 + w1) / 2
        if math.dist(tuple(mid + wall_normal * 0.1), (cx, cy)) < math.dist(tuple(mid - wall_normal * 0.1), (cx, cy)):
            wall_normal = -wall_normal

        entrance_ts = [e["t"] for e in b["entrances"]] if i == b.get("entrance_wall_idx") else []

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
                msp.add_3dface(
                    [
                        (*p_left, z0), (*p_right, z0),
                        (*p_right, z1), (*p_left, z1),
                    ],
                    dxfattribs={"layer": "WINDOWS"},
                )

    # Canopies (козырьки) over each entrance, at first-floor height
    canopy_h = min(floor_h - 0.3, 3.2)
    canopy_depth, canopy_width = 1.4, 2.6
    for ent in b["entrances"]:
        dx, dy = ent["point"].x - ox, ent["point"].y - oy
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
        msp.add_circle(center=(dx, dy, 0.05), radius=0.5, dxfattribs={"layer": "CANOPIES"})

    msp.add_text(
        f"{b['name']} h={h:.1f}m",
        dxfattribs={"layer": "LABELS", "height": 2.0, "insert": (cx, cy)},
    )

doc.set_modelspace_vport(height=territory_coords and (maxy - miny + 60), center=((maxx - minx) / 2, (maxy - miny) / 2))

OUT = "../02_courtyard_3buildings.dxf"
doc.saveas(OUT)
print(f"Saved {OUT}")
print(f"Territory area: {territory.area:.0f} m2")
for b in cluster:
    print(f"  {b['name']}: footprint {b['poly'].area:.0f} m2, height {b['height']:.1f} m ({b['levels']} floors)")
print(f"Parking area: {parking_area.area:.0f} m2 ({len(stall_lines)+1 if not parking_area.is_empty else 0} stalls, anchored on {'real OSM' if was_real else 'synthesized'} location)")
print(f"Grass/lawn area: {grass_area.area:.0f} m2")
print(f"Footpaths: {len(paths)}, total {path_area.area:.0f} m2")
print(f"Entrances: {sum(len(b['entrances']) for b in cluster)} (2 per building, with canopies)")
print(f"Street lamps: {len(lamp_points)}")
n_underground = sum(1 for layer, *_ in utility_lines if layer != GAS_LAYER)
n_gas = sum(1 for layer, *_ in utility_lines if layer == GAS_LAYER)
print(f"Underground utility runs: {n_underground} (water/sewer/heating/cable trunks + branches)")
print(f"Gas pipe runs (above ground): {n_gas}")
