"""
Приводит .glb, экспортированный из Blender, к соглашениям сцены
(frontend/public/models/README.md): основание в (0, 0, 0), центр по X/Z в
нуле, 1 юнит = 1 метр, заданная высота, единичные трансформы узлов.

Зачем запекать трансформы в вершины, а не просто поправить узел: деревья
рисуются через drei.Merged (frontend/src/scene/InstancedVegetation.tsx),
а он берёт из модели только геометрию и материал, трансформ узла
игнорирует. Экспорт из Blender обычно кладёт в узел поворот Z-up -> Y-up,
масштаб объекта и его положение в сцене Blender, поэтому без запекания
инстансы лежали бы на боку, а выделенное дерево (оно рисуется через
scene.clone() с трансформами) стояло бы в стороне от своей точки.

Заодно выбрасывает мусор: пустые узлы без меша и меши, которые на порядки
меньше основного объекта (случайно выделенные при экспорте плоскости и т.п.).

    python tools/normalize_glb.py in.glb out.glb --height 7.3

Зависимости -- только numpy.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np

COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
# Меш считается мусором, если его наибольший размер меньше этой доли от
# наибольшего размера самого крупного меша в файле.
JUNK_EXTENT_RATIO = 0.05


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, _, total = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise SystemExit(f"{path}: не .glb")
    offset, gltf, binary = 12, None, b""
    while offset < total:
        length, ctype = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8: offset + 8 + length]
        if ctype == 0x4E4F534A:
            gltf = json.loads(chunk)
        elif ctype == 0x004E4942:
            binary = chunk
        offset += 8 + length
    return gltf, binary


def write_glb(path: Path, gltf: dict, binary: bytes) -> None:
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(out)


def accessor_array(gltf: dict, binary: bytes, index: int) -> np.ndarray:
    acc = gltf["accessors"][index]
    if "sparse" in acc:
        raise SystemExit("sparse-accessor'ы не поддерживаются")
    fmt, size = COMPONENT[acc["componentType"]]
    n = NCOMP[acc["type"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or size * n
    dtype = np.dtype(f"<{fmt}")
    rows = [np.frombuffer(binary, dtype=dtype, count=n, offset=start + i * stride) for i in range(acc["count"])]
    return np.array(rows).reshape(acc["count"], n) if n > 1 else np.array(rows).reshape(-1)


def node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    rot = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m = np.eye(4)
    m[:3, :3] = rot @ np.diag(node.get("scale", [1, 1, 1]))
    m[:3, 3] = node.get("translation", [0, 0, 0])
    return m


def mesh_nodes(gltf: dict) -> list[tuple[int, np.ndarray]]:
    """(индекс меша, мировая матрица) для каждого узла сцены с мешем."""
    found = []

    def walk(idx: int, parent: np.ndarray) -> None:
        node = gltf["nodes"][idx]
        world = parent @ node_matrix(node)
        if "mesh" in node:
            found.append((node["mesh"], world))
        for child in node.get("children", []):
            walk(child, world)

    for root in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        walk(root, np.eye(4))
    return found


def normalize(src: Path, dst: Path, height: float) -> None:
    gltf, binary = read_glb(src)
    instances = mesh_nodes(gltf)
    if not instances:
        raise SystemExit(f"{src}: в сцене нет ни одного меша")

    # Мировые позиции каждого примитива каждого узла.
    baked = []  # (mesh_idx, prim_idx, positions, normals|None)
    for mesh_idx, world in instances:
        normal_m = np.linalg.inv(world[:3, :3]).T
        for prim_idx, prim in enumerate(gltf["meshes"][mesh_idx]["primitives"]):
            attrs = prim["attributes"]
            if "TANGENT" in attrs:
                raise SystemExit("TANGENT не поддерживается -- экспортируйте без касательных")
            pos = accessor_array(gltf, binary, attrs["POSITION"]).astype(float)
            pos = (world @ np.c_[pos, np.ones(len(pos))].T).T[:, :3]
            nrm = None
            if "NORMAL" in attrs:
                nrm = (normal_m @ accessor_array(gltf, binary, attrs["NORMAL"]).astype(float).T).T
                nrm /= np.linalg.norm(nrm, axis=1, keepdims=True).clip(min=1e-12)
            baked.append((mesh_idx, prim_idx, pos, nrm))

    extent = {}
    for mesh_idx, _, pos, _ in baked:
        lo, hi = extent.get(mesh_idx, (pos.min(0), pos.max(0)))
        extent[mesh_idx] = (np.minimum(lo, pos.min(0)), np.maximum(hi, pos.max(0)))
    biggest = max((hi - lo).max() for lo, hi in extent.values())
    keep = {m for m, (lo, hi) in extent.items() if (hi - lo).max() >= biggest * JUNK_EXTENT_RATIO}
    dropped = sorted(set(extent) - keep)
    baked = [b for b in baked if b[0] in keep]

    all_pos = np.vstack([b[2] for b in baked])
    lo, hi = all_pos.min(0), all_pos.max(0)
    shift = np.array([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
    scale = height / (hi[1] - lo[1])

    # Новый glTF: один узел на меш с единичным трансформом, только нужные
    # accessor'ы/bufferView'ы/материалы, упакованные заново.
    out_binary = bytearray()
    out_views, out_accessors = [], []

    def add_view(raw: bytes, target: int | None = None) -> int:
        out_binary.extend(b"\x00" * (-len(out_binary) % 4))
        view = {"buffer": 0, "byteOffset": len(out_binary), "byteLength": len(raw)}
        if target:
            view["target"] = target
        out_binary.extend(raw)
        out_views.append(view)
        return len(out_views) - 1

    def add_accessor(src_acc: dict, array: np.ndarray, target: int) -> int:
        fmt, _ = COMPONENT[src_acc["componentType"]]
        raw = np.ascontiguousarray(array, dtype=np.dtype(f"<{fmt}")).tobytes()
        acc = {k: v for k, v in src_acc.items() if k not in ("bufferView", "byteOffset", "min", "max")}
        acc["bufferView"] = add_view(raw, target)
        if "min" in src_acc:
            flat = array.reshape(len(array), -1)
            cast = float if fmt == "f" else int
            acc["min"] = [cast(v) for v in flat.min(0)]
            acc["max"] = [cast(v) for v in flat.max(0)]
        out_accessors.append(acc)
        return len(out_accessors) - 1

    material_map: dict[int, int] = {}
    out_materials, out_meshes, out_nodes = [], [], []
    baked_by_key = {(m, p): (pos, nrm) for m, p, pos, nrm in baked}
    for mesh_idx in sorted(keep):
        src_mesh = gltf["meshes"][mesh_idx]
        prims = []
        for prim_idx, prim in enumerate(src_mesh["primitives"]):
            pos, nrm = baked_by_key[(mesh_idx, prim_idx)]
            attrs = {}
            for name, acc_idx in prim["attributes"].items():
                src_acc = gltf["accessors"][acc_idx]
                if name == "POSITION":
                    data = (pos + shift) * scale
                elif name == "NORMAL":
                    data = nrm
                else:
                    data = accessor_array(gltf, binary, acc_idx)
                attrs[name] = add_accessor(src_acc, data, 34962)
            new_prim = {k: v for k, v in prim.items() if k not in ("attributes", "indices", "material")}
            new_prim["attributes"] = attrs
            if "indices" in prim:
                src_acc = gltf["accessors"][prim["indices"]]
                new_prim["indices"] = add_accessor(src_acc, accessor_array(gltf, binary, prim["indices"]), 34963)
            if "material" in prim:
                if prim["material"] not in material_map:
                    material_map[prim["material"]] = len(out_materials)
                    out_materials.append(gltf["materials"][prim["material"]])
                new_prim["material"] = material_map[prim["material"]]
            prims.append(new_prim)
        out_meshes.append({**{k: v for k, v in src_mesh.items() if k != "primitives"}, "primitives": prims})
        out_nodes.append({"name": src_mesh.get("name", f"mesh{mesh_idx}"), "mesh": len(out_meshes) - 1})

    if any("baseColorTexture" in json.dumps(m) or "normalTexture" in json.dumps(m) for m in out_materials):
        raise SystemExit("материалы с текстурами пока не поддерживаются этим скриптом")

    result = {
        "asset": gltf.get("asset", {"version": "2.0"}),
        "scene": 0,
        "scenes": [{"nodes": list(range(len(out_nodes)))}],
        "nodes": out_nodes,
        "meshes": out_meshes,
        "materials": out_materials,
        "accessors": out_accessors,
        "bufferViews": out_views,
        "buffers": [{"byteLength": len(out_binary) + (-len(out_binary) % 4)}],
    }
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in gltf:
            result[key] = gltf[key]
    write_glb(dst, result, bytes(out_binary))

    size = (hi - lo) * scale
    print(
        f"{src.name} -> {dst.name}: высота {size[1]:.2f} м, ширина {size[0]:.2f} x {size[2]:.2f} м "
        f"(масштаб x{scale:.2f}), мешей {len(keep)}, выброшено мешей {len(dropped)}, "
        f"{src.stat().st_size // 1024} -> {dst.stat().st_size // 1024} КБ"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    parser.add_argument("--height", type=float, required=True, help="итоговая высота модели, м")
    args = parser.parse_args()
    normalize(args.src, args.dst, args.height)


if __name__ == "__main__":
    main()
