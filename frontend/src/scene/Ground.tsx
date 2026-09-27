import { useMemo } from "react";
import * as THREE from "three";
import type { Boundary } from "../types";
import { FLAT_LAYER_ORDER, flatPolygonGeometry } from "./geometryHelpers";

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
      <meshStandardMaterial color="#6f8f5c" side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}
