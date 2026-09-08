import { useMemo } from "react";
import * as THREE from "three";
import type { FacadeQuad } from "../types";

// Окна/козырьки приходят как отдельные 4-вершинные грани — в локации 5 их под
// 40000. Рендерить каждую отдельным <mesh> убило бы FPS, поэтому все грани
// одного вида сливаются в один BufferGeometry (один draw call) один раз при
// загрузке сцены.
function mergeQuads(quads: FacadeQuad[]): THREE.BufferGeometry | null {
  if (!quads.length) return null;
  const positions: number[] = [];
  const indices: number[] = [];
  let base = 0;
  for (const q of quads) {
    if (q.length !== 4) continue;
    for (const p of q) positions.push(p.x, p.y, p.z);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
    base += 4;
  }
  if (!positions.length) return null;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

export function Windows({ quads }: { quads: FacadeQuad[] }) {
  const geometry = useMemo(() => mergeQuads(quads), [quads]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color="#4a6b85"
        emissive="#1c2e3d"
        emissiveIntensity={0.3}
        metalness={0.3}
        roughness={0.2}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

export function Canopies({ quads }: { quads: FacadeQuad[] }) {
  const geometry = useMemo(() => mergeQuads(quads), [quads]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry} castShadow>
      <meshStandardMaterial color="#8a8378" side={THREE.DoubleSide} />
    </mesh>
  );
}
