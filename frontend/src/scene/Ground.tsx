import { useMemo } from "react";
import * as THREE from "three";
import type { Boundary, Point2 } from "../types";
import { FLAT_LAYER_ORDER, flatPolygonGeometry } from "./geometryHelpers";

// Земля участка -- приглушённая зелёная; газон (Lawns.tsx) ярче неё. Под ней --
// земля окрестностей, только там, где в чертеже что-то есть: площадки вокруг
// домов и полотна улиц и дорожек (geometry.computeSurroundings). Дома у
// границы не висят в пустоте, а пустое место вокруг ничем не заливается.
// Порядок отрисовки плоских слоёв -- см. FLAT_LAYER_ORDER в geometryHelpers.ts.
const GROUND_Y = -0.1;
const SURROUNDINGS_Y = -0.12;

function mergedFlatGeometry(polygons: Point2[][]): THREE.BufferGeometry | null {
  const parts = polygons.map(flatPolygonGeometry).filter((g): g is THREE.BufferGeometry => g !== null);
  if (!parts.length) return null;
  const merged = mergeGeometries(parts);
  parts.forEach((g) => g.dispose());
  return merged;
}

// Склейка плоских геометрий в одну: один draw call на тысячи дорожек.
function mergeGeometries(parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const indices: number[] = [];
  let base = 0;
  for (const g of parts) {
    const pos = g.getAttribute("position");
    for (let i = 0; i < pos.count; i++) positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
    const index = g.getIndex();
    if (index) for (let i = 0; i < index.count; i++) indices.push(base + index.getX(i));
    else for (let i = 0; i < pos.count; i++) indices.push(base + i);
    base += pos.count;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

function FlatLayer({ geometry, color, y, order }: { geometry: THREE.BufferGeometry | null; color: string; y: number; order: number }) {
  if (!geometry) return null;
  return (
    <mesh geometry={geometry} position={[0, y, 0]} renderOrder={order} receiveShadow>
      <meshStandardMaterial color={color} side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}

export function Ground({
  boundary,
  surroundings,
}: {
  boundary: Boundary | null;
  surroundings: { yards: Point2[][]; streets: Point2[][] };
}) {
  const site = useMemo(() => (boundary ? flatPolygonGeometry(boundary.polygon) : null), [boundary]);
  const yards = useMemo(() => mergedFlatGeometry(surroundings.yards), [surroundings]);
  const streets = useMemo(() => mergedFlatGeometry(surroundings.streets), [surroundings]);

  return (
    <>
      <FlatLayer geometry={yards} color="#a3b394" y={SURROUNDINGS_Y} order={FLAT_LAYER_ORDER.surroundings} />
      <FlatLayer geometry={streets} color="#9c9d98" y={SURROUNDINGS_Y} order={FLAT_LAYER_ORDER.surroundings} />
      <FlatLayer geometry={site} color="#6f8f5c" y={GROUND_Y} order={FLAT_LAYER_ORDER.ground} />
    </>
  );
}
