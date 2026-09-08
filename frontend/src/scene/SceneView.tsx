import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { RestrictionZone, Scene } from "../types";
import { Ground } from "./Ground";
import { RestrictionZones } from "./RestrictionZones";
import { Buildings } from "./Buildings";
import { PlacedObjects } from "./PlacedObjects";

export function SceneView({
  scene,
  selectedId,
  onSelect,
  onMove,
  onHoverZone,
}: {
  scene: Scene;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onHoverZone: (zone: RestrictionZone | null) => void;
}) {
  return (
    <Canvas shadows camera={{ position: [40, 45, 40], fov: 45 }} onPointerMissed={() => onSelect(null)}>
      <color attach="background" args={["#cfe0e8"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[30, 50, 20]} intensity={1.1} castShadow />
      <Ground boundary={scene.boundary} />
      <RestrictionZones zones={scene.restrictions} onHover={onHoverZone} />
      <Buildings objects={scene.objects} />
      <PlacedObjects
        objects={scene.objects}
        restrictions={scene.restrictions}
        selectedId={selectedId}
        onSelect={onSelect}
        onMove={onMove}
      />
      <gridHelper args={[400, 80, "#8fa6b3", "#b9cdd6"]} />
      <OrbitControls makeDefault />
    </Canvas>
  );
}
