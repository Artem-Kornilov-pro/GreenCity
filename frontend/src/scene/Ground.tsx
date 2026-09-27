import { useMemo } from "react";
import * as THREE from "three";
import type { Boundary } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

// Плоские слои участка разнесены по высоте на 3+ см: земля -0.10, газон
// 0.02, зоны 0.04/0.07/0.10 (RestrictionZones.tsx), сетка -0.30
// (SceneView.tsx). На крупной сцене из DWG камера отходит на километры, и
// точности глубины хватает на несколько миллиметров: при прежних зазорах
// 5-10 мм слои перебивали друг друга и мерцали (Берзарина).
const GROUND_Y = -0.1;

export function Ground({ boundary }: { boundary: Boundary | null }) {
  const geometry = useMemo(
    () => (boundary ? flatPolygonGeometry(boundary.polygon) : null),
    [boundary]
  );

  if (!geometry) return null;
  return (
    <mesh geometry={geometry} position={[0, GROUND_Y, 0]} receiveShadow>
      <meshStandardMaterial color="#6f8f5c" side={THREE.DoubleSide} />
    </mesh>
  );
}
