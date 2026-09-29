import { useMemo } from "react";
import * as THREE from "three";
import type { Boundary } from "../types";
import { FLAT_LAYER_ORDER, flatPolygonGeometry } from "./geometryHelpers";

// Основание участка -- нейтрального цвета мощения и грунта, а не зелёное:
// иначе газон (Lawns.tsx) не отличить от дорожек и открытой земли.
// Порядок отрисовки плоских слоёв -- см. FLAT_LAYER_ORDER в geometryHelpers.ts.
const GROUND_Y = -0.1;

export function Ground({ boundary }: { boundary: Boundary | null }) {
  const geometry = useMemo(
    () => (boundary ? flatPolygonGeometry(boundary.polygon) : null),
    [boundary]
  );

  if (!geometry) return null;
  return (
    <mesh geometry={geometry} position={[0, GROUND_Y, 0]} renderOrder={FLAT_LAYER_ORDER.ground} receiveShadow>
      <meshStandardMaterial color="#c9c3b6" side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}
