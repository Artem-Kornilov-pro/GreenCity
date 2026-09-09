// Отрисовка объекта сцены по записи каталога (backend/plant_catalog.py).
//
// Если для записи есть .glb (путь перечислен в frontend/public/models/
// manifest.json, который пишет tools/convert_models.mjs) -- рисуется модель.
// Если нет -- примитив-заглушка по `render.shape` и габаритам из каталога.
// Поэтому добавление пака готовых моделей не требует правок этого файла:
// положили .glb, прогнали конвертацию -- объекты начали рисоваться моделями.

import { Suspense } from "react";
import { useGLTF } from "@react-three/drei";
import type { CatalogItem } from "../catalog";

const VIOLATION_COLOR = "#e0433b";

// Смещения (доля от радиуса кроны) для кластера из 4 сфер разного размера,
// собранных в один пушистый ком вместо одного идеального шара.
const CLUSTER_LOBES: [number, number, number, number][] = [
  [0, 0.55, 0, 1],
  [0.55, 0.15, 0.2, 0.68],
  [-0.5, 0.3, -0.3, 0.62],
  [0.1, 0.65, -0.55, 0.58],
];

function GltfModel({ url }: { url: string }) {
  const { scene } = useGLTF(url);
  // Клон обязателен: загруженный glTF переиспользуется всеми экземплярами
  // этого вида, а один и тот же Object3D нельзя вставить в несколько мест
  // графа сцены -- он "переедет" в последнее.
  return <primitive object={scene.clone()} />;
}

function Trunk({ height }: { height: number }) {
  if (height <= 0) return null;
  return (
    <mesh position={[0, height / 2, 0]} castShadow>
      <cylinderGeometry args={[height * 0.1, height * 0.13, height, 8]} />
      <meshStandardMaterial color="#6b4a2f" />
    </mesh>
  );
}

function Primitive({ item, violated }: { item: CatalogItem; violated: boolean }) {
  const color = violated ? VIOLATION_COLOR : item.render.color;
  const d = item.dimensions;
  const radius = d.radius ?? 0.5;
  const trunkHeight = d.trunk_height ?? 0;
  const crownHeight = Math.max(d.height - trunkHeight, 0.2);

  switch (item.render.shape) {
    case "cone":
      return (
        <group>
          <Trunk height={trunkHeight} />
          <mesh position={[0, trunkHeight + crownHeight / 2, 0]} castShadow>
            <coneGeometry args={[radius, crownHeight, 8]} />
            <meshStandardMaterial color={color} />
          </mesh>
        </group>
      );

    case "pine": {
      // Силуэт сосны/ели -- три сужающихся кверху яруса конусов.
      const tiers = [1, 0.7, 0.42];
      return (
        <group>
          <Trunk height={trunkHeight} />
          {tiers.map((s, i) => (
            <mesh key={i} position={[0, trunkHeight + crownHeight * (0.18 + i * 0.32) * s, 0]} castShadow>
              <coneGeometry args={[radius * s * 1.15, crownHeight * 0.42, 8]} />
              <meshStandardMaterial color={color} />
            </mesh>
          ))}
        </group>
      );
    }

    case "cluster":
      // Лиственная крона кластером сфер: один идеальный шар смотрится как
      // примитивная заготовка, а не как дерево.
      return (
        <group>
          <Trunk height={trunkHeight} />
          {CLUSTER_LOBES.map(([dx, dy, dz, s], i) => (
            <mesh key={i} position={[dx * radius, trunkHeight + dy * radius, dz * radius]} castShadow>
              <sphereGeometry args={[radius * s, 10, 8]} />
              <meshStandardMaterial color={color} />
            </mesh>
          ))}
        </group>
      );

    case "sphere":
      return (
        <group>
          <mesh position={[0, radius * 0.55, 0]} castShadow>
            <sphereGeometry args={[radius, 10, 8]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh position={[radius * 0.45, radius * 0.35, radius * 0.2]} castShadow>
            <sphereGeometry args={[radius * 0.6, 8, 7]} />
            <meshStandardMaterial color={color} />
          </mesh>
        </group>
      );

    case "box":
      return (
        <group>
          <mesh position={[0, d.height / 2, 0]} castShadow>
            <boxGeometry args={[d.width ?? 2, d.height, d.depth ?? 0.6]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh position={[0, d.height + 0.06, 0]} castShadow>
            <boxGeometry args={[d.width ?? 2, 0.12, d.depth ?? 0.6]} />
            <meshStandardMaterial color={violated ? VIOLATION_COLOR : "#568a4f"} />
          </mesh>
        </group>
      );

    // Плоские участки: газон, цветник, плитка дорожки.
    case "patch":
      return (
        <mesh position={[0, d.height / 2, 0]} receiveShadow>
          <boxGeometry args={[d.width ?? 2, d.height, d.depth ?? 2]} />
          <meshStandardMaterial color={color} />
        </mesh>
      );

    case "bench":
      return (
        <mesh position={[0, d.height / 2, 0]} castShadow>
          <boxGeometry args={[d.width ?? 1.4, d.height, d.depth ?? 0.5]} />
          <meshStandardMaterial color={color} />
        </mesh>
      );

    case "lamp":
      return (
        <group>
          <mesh position={[0, d.height / 2, 0]}>
            <cylinderGeometry args={[0.05, 0.05, d.height, 6]} />
            <meshStandardMaterial color="#555555" />
          </mesh>
          <mesh position={[0, d.height, 0]}>
            <sphereGeometry args={[radius, 8, 8]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} />
          </mesh>
        </group>
      );

    case "trash":
      return (
        <group>
          <mesh position={[0, d.height / 2, 0]} castShadow>
            <cylinderGeometry args={[radius * 0.92, radius * 0.77, d.height, 10]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh position={[0, d.height + 0.02, 0]}>
            <cylinderGeometry args={[radius, radius, 0.05, 10]} />
            <meshStandardMaterial color="#2a332a" />
          </mesh>
        </group>
      );

    case "fountain":
      return (
        <group>
          <mesh position={[0, 0.15, 0]} castShadow>
            <cylinderGeometry args={[radius * 0.92, radius, 0.3, 20]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh position={[0, 0.32, 0]}>
            <cylinderGeometry args={[radius * 0.79, radius * 0.79, 0.08, 20]} />
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

    default:
      return (
        <mesh position={[0, 0.3, 0]}>
          <boxGeometry args={[0.5, 0.6, 0.5]} />
          <meshStandardMaterial color="#999999" />
        </mesh>
      );
  }
}

// Структурные объекты, которых нет в каталоге (пользователь их не расставляет):
// подъезды и площадки приходят из DXF-подосновы.
function StructuralVisual({ type }: { type: string }) {
  if (type === "entrance") {
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
  }
  if (type === "playground") {
    return (
      <mesh position={[0, 0.1, 0]}>
        <boxGeometry args={[2, 0.2, 2]} />
        <meshStandardMaterial color="#e08a3c" />
      </mesh>
    );
  }
  return (
    <mesh position={[0, 0.3, 0]}>
      <boxGeometry args={[0.5, 0.6, 0.5]} />
      <meshStandardMaterial color="#999999" />
    </mesh>
  );
}

export function ObjectVisual({
  type,
  item,
  hasModel,
  violated,
}: {
  type: string;
  item?: CatalogItem;
  hasModel: boolean;
  violated: boolean;
}) {
  if (!item) return <StructuralVisual type={type} />;

  // Нарушение отступа подсвечивается цветом, а на .glb-модели цвет так просто
  // не подменить -- поэтому в состоянии нарушения намеренно рисуем примитив:
  // увидеть проблему важнее, чем красивую модель.
  if (!hasModel || violated) return <Primitive item={item} violated={violated} />;

  return (
    <Suspense fallback={<Primitive item={item} violated={false} />}>
      <GltfModel url={item.model} />
    </Suspense>
  );
}
