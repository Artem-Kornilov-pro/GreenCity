import { useMemo } from "react";
import * as THREE from "three";
import type { Boundary } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

export function Ground({ boundary }: { boundary: Boundary | null }) {
  const geometry = useMemo(
    () => (boundary ? flatPolygonGeometry(boundary.polygon) : null),
    [boundary]
  );

  if (!geometry) return null;
  return (
    <mesh geometry={geometry} position={[0, -0.02, 0]} receiveShadow>
      <meshStandardMaterial color="#6f8f5c" side={THREE.DoubleSide} />
    </mesh>
  );
}
