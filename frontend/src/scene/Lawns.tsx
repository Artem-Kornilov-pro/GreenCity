import { useMemo } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { LawnArea } from "../types";

// Газон GreenPlan -- заливка поверх земли участка (Ground.tsx), ниже зон
// ограничений (RestrictionZones.tsx, Y от 0.01): зоны остаются читаемыми и
// наводимыми мышью. Клумбы кустарника -- дырками в шейпе. Все участки газона
// одного статуса слиты в одну геометрию: на крупных сценах их сотни.
const LAWN_Y = 0.005;
const COLOR_BY_STATUS: Record<LawnArea["status"], string> = {
  new: "#7cc35a",
  existing: "#5f9a48",
};

function mergeLawns(lawns: LawnArea[]): THREE.BufferGeometry | null {
  const parts: THREE.BufferGeometry[] = [];
  for (const lawn of lawns) {
    if (lawn.polygon.length < 3) continue;
    const shape = new THREE.Shape(lawn.polygon.map((p) => new THREE.Vector2(p.x, -p.z)));
    for (const hole of lawn.holes) {
      if (hole.length >= 3) shape.holes.push(new THREE.Path(hole.map((p) => new THREE.Vector2(p.x, -p.z))));
    }
    parts.push(new THREE.ShapeGeometry(shape));
  }
  if (!parts.length) return null;
  const merged = mergeGeometries(parts);
  for (const part of parts) part.dispose();
  // Тот же разворот, что у flatPolygonGeometry (geometryHelpers.ts).
  merged?.rotateX(-Math.PI / 2);
  return merged;
}

export function Lawns({ lawns }: { lawns: LawnArea[] }) {
  const groups = useMemo(
    () =>
      (["existing", "new"] as const)
        .map((status) => ({ status, geometry: mergeLawns(lawns.filter((l) => l.status === status)) }))
        .filter((g): g is { status: LawnArea["status"]; geometry: THREE.BufferGeometry } => g.geometry !== null),
    [lawns]
  );

  return (
    <>
      {groups.map(({ status, geometry }) => (
        <mesh key={status} geometry={geometry} position={[0, LAWN_Y, 0]} receiveShadow>
          <meshStandardMaterial color={COLOR_BY_STATUS[status]} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </>
  );
}
