import { useEffect, useMemo } from "react";
import * as THREE from "three";
import type { Point2, SceneObject } from "../types";
import { extrudedPolygonGeometry } from "./geometryHelpers";

// Здания неподвижны и одинаковы, поэтому сливаются в одну геометрию с одним
// материалом: один вызов отрисовки вместо сотен.
function mergeBuildings(objects: SceneObject[]): THREE.BufferGeometry | null {
  const positions: number[] = [];
  const normals: number[] = [];

  for (const obj of objects) {
    if (obj.type !== "building") continue;
    const footprint = (obj.metadata.footprint as Point2[] | undefined) ?? [];
    const height = (obj.metadata.height as number | undefined) ?? 9;
    const geo = extrudedPolygonGeometry(footprint, height);
    if (!geo) continue;

    // ExtrudeGeometry отдаёт индексированную геометрию -- разворачиваем, чтобы
    // складывать вершины подряд без пересчёта индексов.
    const flat = geo.index ? geo.toNonIndexed() : geo;
    const pos = flat.getAttribute("position");
    const nrm = flat.getAttribute("normal");
    for (let i = 0; i < pos.count; i++) {
      positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      normals.push(nrm.getX(i), nrm.getY(i), nrm.getZ(i));
    }
    if (flat !== geo) flat.dispose();
    geo.dispose();
  }

  if (!positions.length) return null;
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  merged.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  return merged;
}

export function Buildings({ objects }: { objects: SceneObject[] }) {
  const geometry = useMemo(() => mergeBuildings(objects), [objects]);

  // Слитая геометрия живёт в памяти GPU и сама не освобождается при смене
  // сцены -- без этого каждая загрузка нового файла оставляла бы предыдущую.
  useEffect(() => () => geometry?.dispose(), [geometry]);

  if (!geometry) return null;
  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color="#b7b0a3" />
    </mesh>
  );
}
