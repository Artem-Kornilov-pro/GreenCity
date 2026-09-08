import { useMemo } from "react";
import * as THREE from "three";
import type { RestrictionZone } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

const COLOR_BY_SEVERITY: Record<string, string> = {
  forbidden: "#e0433b",
  warning: "#e8a83c",
  allowed: "#3fae5a",
};

export function RestrictionZones({
  zones,
  onHover,
}: {
  zones: RestrictionZone[];
  onHover: (zone: RestrictionZone | null) => void;
}) {
  return (
    <>
      {zones.map((zone, i) => (
        <ZoneMesh key={zone.id} zone={zone} yOffset={0.01 + i * 0.0004} onHover={onHover} />
      ))}
    </>
  );
}

function ZoneMesh({
  zone,
  yOffset,
  onHover,
}: {
  zone: RestrictionZone;
  yOffset: number;
  onHover: (zone: RestrictionZone | null) => void;
}) {
  const geometry = useMemo(() => flatPolygonGeometry(zone.polygon), [zone]);
  if (!geometry) return null;

  return (
    <mesh
      geometry={geometry}
      position={[0, yOffset, 0]}
      onPointerOver={(e) => {
        e.stopPropagation();
        onHover(zone);
      }}
      onPointerOut={(e) => {
        e.stopPropagation();
        onHover(null);
      }}
    >
      <meshBasicMaterial
        color={COLOR_BY_SEVERITY[zone.severity] ?? "#999999"}
        transparent
        opacity={0.35}
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}
