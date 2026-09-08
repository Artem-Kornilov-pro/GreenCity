// Реальных 3D-моделей (glb) пока нет — parser/parse_dxf.py уже прописывает
// пути вида /models/tree.glb на будущее, а сейчас каждый тип объекта рисуется
// простым примитивом, узнаваемым по силуэту и цвету.

export function ObjectVisual({ type, violated }: { type: string; violated: boolean }) {
  switch (type) {
    case "tree":
      return (
        <group>
          <mesh position={[0, 0.6, 0]} castShadow>
            <cylinderGeometry args={[0.12, 0.16, 1.2, 8]} />
            <meshStandardMaterial color="#6b4a2f" />
          </mesh>
          <mesh position={[0, 1.6, 0]} castShadow>
            <coneGeometry args={[0.9, 1.8, 8]} />
            <meshStandardMaterial color={violated ? "#e0433b" : "#2e7d3a"} />
          </mesh>
        </group>
      );
    case "bush":
      return (
        <mesh position={[0, 0.35, 0]} castShadow>
          <sphereGeometry args={[0.5, 10, 10]} />
          <meshStandardMaterial color={violated ? "#e0433b" : "#4a9450"} />
        </mesh>
      );
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
