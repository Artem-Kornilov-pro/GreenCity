import { useMemo } from "react";
import type { Point2, SceneObject } from "../types";
import { extrudedPolygonGeometry } from "./geometryHelpers";

export function Buildings({ objects }: { objects: SceneObject[] }) {
  const buildings = objects.filter((o) => o.type === "building");
  return (
    <>
      {buildings.map((b) => (
        <BuildingMesh key={b.id} obj={b} />
      ))}
    </>
  );
}

function BuildingMesh({ obj }: { obj: SceneObject }) {
  const footprint = (obj.metadata.footprint as Point2[] | undefined) ?? [];
  const height = (obj.metadata.height as number | undefined) ?? 9;
  const geometry = useMemo(() => extrudedPolygonGeometry(footprint, height), [footprint, height]);
  if (!geometry) return null;

  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color="#b7b0a3" />
    </mesh>
  );
}
