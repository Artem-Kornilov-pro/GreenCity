// Реальных 3D-моделей (glb) пока нет — parser/parse_dxf.py уже прописывает
// пути вида /models/tree.glb на будущее, а сейчас каждый тип объекта рисуется
// простым примитивом, узнаваемым по силуэту и цвету.

import type { ReactNode } from "react";

export type TreeKind = "medium" | "tall" | "short" | "pine" | "round" | "round_large";

interface TreePreset {
  label: string;
  trunkHeight: number;
  trunkRadius: [number, number];
  crownHeight: number;
  crownRadius: number;
  crownShape: "cone" | "cluster" | "pine";
  color: string;
}

// "средние"/"высокие"/"низкие" — варианты размера с конической кроной;
// "конусовые сосны" — узкая многоярусная крона отдельным силуэтом;
// "круглые" — лиственная крона кластером сфер (не один плоский шар — иначе
// смотрится как примитивная заготовка, а не дерево), в двух размерах.
export const TREE_PRESETS: Record<TreeKind, TreePreset> = {
  medium: {
    label: "Дерево — среднее",
    trunkHeight: 1.2,
    trunkRadius: [0.12, 0.16],
    crownHeight: 1.8,
    crownRadius: 0.9,
    crownShape: "cone",
    color: "#2e7d3a",
  },
  tall: {
    label: "Дерево — высокое",
    trunkHeight: 2.0,
    trunkRadius: [0.14, 0.18],
    crownHeight: 2.6,
    crownRadius: 1.1,
    crownShape: "cone",
    color: "#2e7d3a",
  },
  short: {
    label: "Дерево — низкое",
    trunkHeight: 0.6,
    trunkRadius: [0.1, 0.13],
    crownHeight: 1.0,
    crownRadius: 0.6,
    crownShape: "cone",
    color: "#3f9146",
  },
  pine: {
    label: "Сосна (конусовая)",
    trunkHeight: 1.4,
    trunkRadius: [0.12, 0.15],
    crownHeight: 3.2,
    crownRadius: 0.7,
    crownShape: "pine",
    color: "#1f5c33",
  },
  round: {
    label: "Дерево — круглая крона",
    trunkHeight: 1.2,
    trunkRadius: [0.12, 0.16],
    crownHeight: 1.6,
    crownRadius: 0.85,
    crownShape: "cluster",
    color: "#3a8f45",
  },
  round_large: {
    label: "Дерево — крупная круглая крона",
    trunkHeight: 1.6,
    trunkRadius: [0.16, 0.2],
    crownHeight: 2.2,
    crownRadius: 1.35,
    crownShape: "cluster",
    color: "#357f40",
  },
};

// Смещения (доля от crownRadius) для кластера из 4 сфер разного размера,
// собранных в один пушистый ком вместо одного идеального шара.
const CLUSTER_LOBES: [number, number, number, number][] = [
  [0, 0.55, 0, 1],
  [0.55, 0.15, 0.2, 0.68],
  [-0.5, 0.3, -0.3, 0.62],
  [0.1, 0.65, -0.55, 0.58],
];

export type BushKind = "medium" | "tall" | "short";

interface BushPreset {
  label: string;
  radius: number;
  color: string;
}

export const BUSH_PRESETS: Record<BushKind, BushPreset> = {
  medium: { label: "Кустарник — средний", radius: 0.5, color: "#4a9450" },
  tall: { label: "Кустарник — высокий", radius: 0.75, color: "#3f8a46" },
  short: { label: "Кустарник — низкий", radius: 0.32, color: "#5aa15f" },
};

function isTreeKind(value: unknown): value is TreeKind {
  return typeof value === "string" && value in TREE_PRESETS;
}

function isBushKind(value: unknown): value is BushKind {
  return typeof value === "string" && value in BUSH_PRESETS;
}

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
