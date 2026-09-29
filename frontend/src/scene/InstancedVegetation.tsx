// GPU-инстансирование повторяющихся .glb-моделей: тысячи деревьев и кустов
// после GreenPlan отдельными мешами роняли FPS.
//
// drei.Merged строит по <Instances> на каждый меш модели (ствол, крона -- с
// разными материалами): один draw call на меш вида, а не на каждое дерево.
//
// limit передаётся явно: по умолчанию Instances обрезает до 1000 экземпляров.
//
// frustumCulled={false} -- исправление, а не оптимизация: three.js считает
// ограничивающую сферу InstancedMesh один раз, по первому кадру, и посадка в
// другой части участка отсекалась целиком.

import { Merged, useGLTF } from "@react-three/drei";
import { memo } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { modelFitScale } from "./geometryHelpers";

export type InstancePlacement = {
  id: string;
  position: [number, number, number];
  rotation: number;
  scale: number;
  // Высота из каталога, под которую подгоняется модель (см. modelFitScale).
  height?: number;
};

// Ёмкость группы. drei.Instances выделяет буфер матриц один раз, при первом
// показе, и не расширяет его: объект сверх ёмкости не рисуется (перенесённое
// или добавленное дерево пропадало, а с ним и вся группа). Ёмкость -- с
// запасом до степени двойки; когда её не хватает, группа пересоздаётся
// (key), а не при каждом изменении числа объектов.
function capacityFor(count: number): number {
  return Math.max(16, 2 ** Math.ceil(Math.log2(Math.max(count, 1))));
}

// memo: placements пересобирается в useMemo вызывающего кода только при
// изменении объектов или выделения.
export const InstancedVegetationGroup = memo(function InstancedVegetationGroup({
  url,
  placements,
  onSelect,
}: {
  url: string;
  placements: InstancePlacement[];
  onSelect: (id: string) => void;
}) {
  const { nodes, scene } = useGLTF(url);
  const capacity = capacityFor(placements.length);

  return (
    <Merged key={capacity} meshes={nodes} limit={capacity} frustumCulled={false}>
      {(Model: Record<string, React.ComponentType<{ position?: [number, number, number]; rotation?: [number, number, number]; scale?: number; onClick?: (e: ThreeEvent<MouseEvent>) => void }>>) => {
        const parts = Object.values(Model);
        // Без группы-обёртки на размещение: каждая часть модели обрабатывает
        // клик сама, а лишний Object3D на тысячи размещений не нужен.
        return (
          <>
            {placements.flatMap((p) =>
              parts.map((Part, i) => (
                <Part
                  key={`${p.id}-${i}`}
                  position={p.position}
                  rotation={[0, p.rotation, 0]}
                  scale={p.scale * modelFitScale(scene, p.height)}
                  onClick={(e: ThreeEvent<MouseEvent>) => {
                    e.stopPropagation();
                    onSelect(p.id);
                  }}
                />
              ))
            )}
          </>
        );
      }}
    </Merged>
  );
});
