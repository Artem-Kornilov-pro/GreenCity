// Реальных 3D-моделей (glb) пока нет — parser/parse_dxf.py уже прописывает
// пути вида /models/tree.glb на будущее, а сейчас каждый тип объекта рисуется
// простым примитивом, узнаваемым по силуэту и цвету.

import type { ReactNode } from "react";

import { CLUSTER_LOBES, TREE_PRESETS, BUSH_PRESETS, isTreeKind, isBushKind } from "./plantPresets";

function ClusterCrown({ radius, baseY, color }: { radius: number; baseY: number; color: string }) {
  return (
    <group>
      {CLUSTER_LOBES.map(([dx, dy, dz, scale], i) => (
        <mesh key={i} position={[dx * radius, baseY + dy * radius, dz * radius]} castShadow>
          <sphereGeometry args={[radius * scale, 10, 8]} />
          <meshStandardMaterial color={color} />
        </mesh>
      ))}
    </group>
  );
}

function Tree({ treeKind, violated }: { treeKind: unknown; violated: boolean }) {
  const preset = TREE_PRESETS[isTreeKind(treeKind) ? treeKind : "medium"];
  const crownColor = violated ? "#e0433b" : preset.color;
  const crownBaseY = preset.trunkHeight;

  let crown: ReactNode;
  if (preset.crownShape === "cluster") {
    crown = <ClusterCrown radius={preset.crownRadius} baseY={crownBaseY} color={crownColor} />;
  } else if (preset.crownShape === "pine") {
    // Классический силуэт сосны/ели — три сужающихся кверху яруса конусов.
    const tiers = [1, 0.7, 0.42];
    crown = (
      <group>
        {tiers.map((scale, i) => (
          <mesh
            key={i}
            position={[0, crownBaseY + preset.crownHeight * (0.18 + i * 0.32) * scale, 0]}
            castShadow
          >
            <coneGeometry args={[preset.crownRadius * scale * 1.15, preset.crownHeight * 0.42, 8]} />
            <meshStandardMaterial color={crownColor} />
          </mesh>
        ))}
      </group>
    );
  } else {
    crown = (
      <mesh position={[0, crownBaseY + preset.crownHeight / 2, 0]} castShadow>
        <coneGeometry args={[preset.crownRadius, preset.crownHeight, 8]} />
        <meshStandardMaterial color={crownColor} />
      </mesh>
    );
  }

  return (
    <group>
      <mesh position={[0, preset.trunkHeight / 2, 0]} castShadow>
        <cylinderGeometry args={[preset.trunkRadius[0], preset.trunkRadius[1], preset.trunkHeight, 8]} />
        <meshStandardMaterial color="#6b4a2f" />
      </mesh>
      {crown}
    </group>
  );
}

function Bush({ bushKind, violated }: { bushKind: unknown; violated: boolean }) {
  const preset = BUSH_PRESETS[isBushKind(bushKind) ? bushKind : "medium"];
  const color = violated ? "#e0433b" : preset.color;
  return (
    <group>
      <mesh position={[0, preset.radius * 0.55, 0]} castShadow>
        <sphereGeometry args={[preset.radius, 10, 8]} />
        <meshStandardMaterial color={color} />
      </mesh>
      <mesh position={[preset.radius * 0.45, preset.radius * 0.35, preset.radius * 0.2]} castShadow>
        <sphereGeometry args={[preset.radius * 0.6, 8, 7]} />
        <meshStandardMaterial color={color} />
      </mesh>
    </group>
  );
}

export function ObjectVisual({
  type,
  violated,
  metadata,
}: {
  type: string;
  violated: boolean;
  metadata?: Record<string, unknown>;
}) {
  switch (type) {
    case "tree":
      return <Tree treeKind={metadata?.treeKind} violated={violated} />;
    case "bush":
      return <Bush bushKind={metadata?.bushKind} violated={violated} />;
    case "bench":
      return (
        <mesh position={[0, 0.25, 0]} castShadow>
          <boxGeometry args={[1.4, 0.4, 0.5]} />
          <meshStandardMaterial color={violated ? "#e0433b" : "#7a5230"} />
        </mesh>
      );
    case "lamp":
      return (
        <group>
          <mesh position={[0, 1.5, 0]}>
            <cylinderGeometry args={[0.05, 0.05, 3, 6]} />
            <meshStandardMaterial color="#555555" />
          </mesh>
          <mesh position={[0, 3, 0]}>
            <sphereGeometry args={[0.2, 8, 8]} />
            <meshStandardMaterial
              color={violated ? "#e0433b" : "#f5e28a"}
              emissive={violated ? "#e0433b" : "#f5e28a"}
              emissiveIntensity={0.6}
            />
          </mesh>
        </group>
      );
    case "trash":
      return (
        <group>
          <mesh position={[0, 0.35, 0]} castShadow>
            <cylinderGeometry args={[0.24, 0.2, 0.6, 10]} />
            <meshStandardMaterial color={violated ? "#e0433b" : "#3d4a3d"} />
          </mesh>
          <mesh position={[0, 0.66, 0]}>
            <cylinderGeometry args={[0.26, 0.26, 0.05, 10]} />
            <meshStandardMaterial color="#2a332a" />
          </mesh>
        </group>
      );
    case "fountain":
      return (
        <group>
          <mesh position={[0, 0.15, 0]} castShadow>
            <cylinderGeometry args={[1.1, 1.2, 0.3, 20]} />
            <meshStandardMaterial color={violated ? "#e0433b" : "#9aa0a6"} />
          </mesh>
          <mesh position={[0, 0.32, 0]}>
            <cylinderGeometry args={[0.95, 0.95, 0.08, 20]} />
            <meshStandardMaterial color="#4a90c4" transparent opacity={0.85} />
          </mesh>
          <mesh position={[0, 0.55, 0]}>
            <cylinderGeometry args={[0.08, 0.1, 0.5, 8]} />
            <meshStandardMaterial color="#9aa0a6" />
          </mesh>
          <mesh position={[0, 0.9, 0]}>
            <coneGeometry args={[0.15, 0.35, 8]} />
            <meshStandardMaterial color="#bcdcf0" transparent opacity={0.6} />
          </mesh>
        </group>
      );
    case "path_segment":
      return (
        <mesh position={[0, 0.03, 0]} receiveShadow>
          <boxGeometry args={[2, 0.06, 1.2]} />
          <meshStandardMaterial color={violated ? "#e0433b" : "#b7ada0"} />
        </mesh>
      );
    case "hedge_segment":
      return (
        <group>
          <mesh position={[0, 0.45, 0]} castShadow>
            <boxGeometry args={[2, 0.9, 0.6]} />
            <meshStandardMaterial color={violated ? "#e0433b" : "#4a7a44"} />
          </mesh>
          <mesh position={[0, 0.92, 0]} castShadow>
            <boxGeometry args={[2, 0.12, 0.6]} />
            <meshStandardMaterial color={violated ? "#e0433b" : "#568a4f"} />
          </mesh>
        </group>
      );
    case "entrance":
      return (
        <group>
          <mesh position={[0, 1.05, 0]}>
            <boxGeometry args={[1.2, 2.1, 0.15]} />
            <meshStandardMaterial color="#2b2f36" />
          </mesh>
          <mesh position={[0, 1.05, 0.08]}>
            <boxGeometry args={[0.9, 1.9, 0.05]} />
            <meshStandardMaterial color="#5b463a" />
          </mesh>
        </group>
      );
    case "playground":
      return (
        <mesh position={[0, 0.1, 0]}>
          <boxGeometry args={[2, 0.2, 2]} />
          <meshStandardMaterial color="#e08a3c" />
        </mesh>
      );
    default:
      return (
        <mesh position={[0, 0.3, 0]}>
          <boxGeometry args={[0.5, 0.6, 0.5]} />
          <meshStandardMaterial color="#999999" />
        </mesh>
      );
  }
}
