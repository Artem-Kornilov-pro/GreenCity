#!/usr/bin/env python3
"""
Разрезание одного .obj с множеством объектов на отдельные .obj -- по одному
на модель.

Зачем: готовые паки часто идут одним файлом, внутри которого сотни моделей
(`o 01_Mesh`, `o 02_Mesh`, ...), расставленных сеткой в мировых координатах.
Каталогу нужна отдельная модель на запись, стоящая основанием в начале
координат, иначе дерево при постановке улетит туда, где оно лежало в паке.

Что делает:
  * разбивает по строкам `o <имя>`;
  * для каждого объекта оставляет только используемые им v/vt/vn и
    перенумеровывает индексы граней (в .obj они сквозные по файлу);
  * нормализует положение -- центрирует по X/Z и опускает основание на Y=0.

Использование:
    python3 tools/split_obj.py input.obj output_dir/ [--mtllib name.mtl]
"""

from __future__ import annotations

import argparse
from pathlib import Path


class ObjObject:
    def __init__(self, name: str):
        self.name = name
        self.faces: list[str] = []
        self.material: str | None = None


def parse_obj(path: Path) -> tuple[list[str], list[str], list[str], list[ObjObject], str | None]:
    """Вернуть (вершины, uv, нормали, объекты, имя mtllib)."""
    vertices: list[str] = []
    uvs: list[str] = []
    normals: list[str] = []
    objects: list[ObjObject] = []
    mtllib: str | None = None
    current: ObjObject | None = None

    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("v "):
                vertices.append(line)
            elif line.startswith("vt "):
                uvs.append(line)
            elif line.startswith("vn "):
                normals.append(line)
            elif line.startswith("o "):
                current = ObjObject(line[2:].strip())
                objects.append(current)
            elif line.startswith("usemtl ") and current is not None:
                current.material = line[7:].strip()
            elif line.startswith("f ") and current is not None:
                current.faces.append(line)
            elif line.startswith("mtllib "):
                mtllib = line[7:].strip()

    return vertices, uvs, normals, objects, mtllib


def parse_face_token(token: str) -> tuple[int, int | None, int | None]:
    """`f` в .obj: v, v/vt, v//vn или v/vt/vn. Индексы 1-based; отрицательные
    (относительные) в экспортах Blender не встречаются, но проверяем явно."""
    parts = token.split("/")
    v = int(parts[0])
    vt = int(parts[1]) if len(parts) > 1 and parts[1] else None
    vn = int(parts[2]) if len(parts) > 2 and parts[2] else None
    if v < 0 or (vt is not None and vt < 0) or (vn is not None and vn < 0):
        raise ValueError("Относительные (отрицательные) индексы в .obj не поддерживаются")
    return v, vt, vn


def write_object(
    obj: ObjObject,
    vertices: list[str],
    uvs: list[str],
    normals: list[str],
    mtllib: str | None,
    out_path: Path,
) -> None:
    used_v: dict[int, int] = {}
    used_vt: dict[int, int] = {}
    used_vn: dict[int, int] = {}
    faces: list[list[tuple[int, int | None, int | None]]] = []

    for face_line in obj.faces:
        face: list[tuple[int, int | None, int | None]] = []
        for token in face_line.split()[1:]:
            v, vt, vn = parse_face_token(token)
            if v not in used_v:
                used_v[v] = len(used_v) + 1
            if vt is not None and vt not in used_vt:
                used_vt[vt] = len(used_vt) + 1
            if vn is not None and vn not in used_vn:
                used_vn[vn] = len(used_vn) + 1
            face.append((v, vt, vn))
        faces.append(face)

    # Нормализация: центр по X/Z в нуле, основание на Y=0 -- иначе модель
    # встанет там, где она лежала в общей сетке пака, а не там, куда её ставят.
    coords = [tuple(float(c) for c in vertices[v - 1].split()[1:4]) for v in used_v]
    min_x = min(c[0] for c in coords)
    max_x = max(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    min_z = min(c[2] for c in coords)
    max_z = max(c[2] for c in coords)
    off_x = (min_x + max_x) / 2
    off_y = min_y
    off_z = (min_z + max_z) / 2

    lines: list[str] = [f"# split from pack: {obj.name}"]
    if mtllib:
        lines.append(f"mtllib {mtllib}")
    lines.append(f"o {obj.name}")

    for original_index in used_v:
        x, y, z = (float(c) for c in vertices[original_index - 1].split()[1:4])
        lines.append(f"v {x - off_x:.6f} {y - off_y:.6f} {z - off_z:.6f}")
    for original_index in used_vt:
        lines.append(uvs[original_index - 1])
    for original_index in used_vn:
        lines.append(normals[original_index - 1])

    if obj.material:
        lines.append(f"usemtl {obj.material}")

    for face in faces:
        tokens = []
        for v, vt, vn in face:
            token = str(used_v[v])
            if vt is not None and vn is not None:
                token += f"/{used_vt[vt]}/{used_vn[vn]}"
            elif vt is not None:
                token += f"/{used_vt[vt]}"
            elif vn is not None:
                token += f"//{used_vn[vn]}"
            tokens.append(token)
        lines.append("f " + " ".join(tokens))

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Исходный .obj с множеством объектов")
    ap.add_argument("output_dir", help="Куда положить отдельные .obj")
    ap.add_argument("--mtllib", default=None, help="Переопределить имя .mtl в результате")
    args = ap.parse_args()

    src = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    vertices, uvs, normals, objects, mtllib = parse_obj(src)
    mtllib = args.mtllib or mtllib
    print(f"Вершин: {len(vertices)}, UV: {len(uvs)}, нормалей: {len(normals)}, объектов: {len(objects)}")

    written = 0
    for obj in objects:
        if not obj.faces:
            continue
        safe = "".join(ch if ch.isalnum() else "_" for ch in obj.name).strip("_").lower()
        write_object(obj, vertices, uvs, normals, mtllib, out_dir / f"{safe}.obj")
        written += 1

    print(f"Записано отдельных .obj: {written} -> {out_dir}")


if __name__ == "__main__":
    main()
