import { useMemo } from "react";
import * as THREE from "three";
import type { CurbPolyline } from "../types";

// Бордюры -- лента вдоль полилинии с одной видимой боковой гранью, все
// отрезки в одной геометрии (как окна в FacadeFeatures.tsx).
const CURB_HEIGHT_M = 0.15;
const CURB_HALF_WIDTH_M = 0.075;

function mergeCurbs(polylines: CurbPolyline[]): THREE.BufferGeometry | null {
  if (!polylines.length) return null;
  const positions: number[] = [];
  const indices: number[] = [];
  let base = 0;

  for (const pl of polylines) {
    if (pl.length < 2) continue;
    for (let i = 0; i < pl.length - 1; i++) {
      const a = pl[i];
      const b = pl[i + 1];
      const dx = b.x - a.x;
      const dz = b.z - a.z;
      const len = Math.hypot(dx, dz);
      if (len < 1e-6) continue;
      const nx = (-dz / len) * CURB_HALF_WIDTH_M;
      const nz = (dx / len) * CURB_HALF_WIDTH_M;

      // верхняя грань -- приподнятая полоса шириной 2*CURB_HALF_WIDTH_M
      const top = [
        [a.x + nx, CURB_HEIGHT_M, a.z + nz],
        [b.x + nx, CURB_HEIGHT_M, b.z + nz],
        [b.x - nx, CURB_HEIGHT_M, b.z - nz],
        [a.x - nx, CURB_HEIGHT_M, a.z - nz],
      ];
      for (const p of top) positions.push(p[0], p[1], p[2]);
      indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
      base += 4;

      // боковая грань с внешней стороны (от земли до верха) -- даёт объём
      const wall = [
        [a.x + nx, 0, a.z + nz],
        [b.x + nx, 0, b.z + nz],
        [b.x + nx, CURB_HEIGHT_M, b.z + nz],
        [a.x + nx, CURB_HEIGHT_M, a.z + nz],
      ];
      for (const p of wall) positions.push(p[0], p[1], p[2]);
      indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
      base += 4;
    }
  }

  if (!positions.length) return null;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

export function Curbs({ polylines }: { polylines: CurbPolyline[] }) {
  const geometry = useMemo(() => mergeCurbs(polylines), [polylines]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color="#c9c5bd" side={THREE.DoubleSide} />
    </mesh>
  );
}
