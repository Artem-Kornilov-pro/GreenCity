// GPU-инстансирование повторяющихся .glb-моделей (perf follow-up: на сцене в
// тысячи деревьев/кустов после GreenPlan-расстановки каждый объект раньше был
// своим mesh -- своим draw call, см. GltfModel в ObjectVisual.tsx. На слабом
// железе (Ryzen 8 ядер/3ГГц, 32 ГБ RAM, без выделенной GPU-мощи M-серии Apple)
// это ощутимо роняло FPS уже на паре тысяч объектов, задокументированный
// репорт при ручном тестировании).
//
// drei.Merged -- ровно для этого случая: реальные .glb (проверено на
// spruce_1/poplartree_1/bench_a) состоят из НЕСКОЛЬКИХ mesh-примитивов
// (ствол/крона разными материалами, части лавки и т.п.), а не одного -- Merged
// сам строит по одному <Instances> (batched InstancedMesh) на каждый
// mesh-примитив исходной модели и на каждое "логическое" размещение
// синхронно рендерит все эти инстансы с одной трансформацией. В сумме --
// один draw call на mesh-примитив вида, а не один на каждое дерево.
//
// limit нужно передавать явно: у Instances по умолчанию limit=1000 --
// вида с числом размещений больше тысячи (сотни бушей одного вида на большом
// участке -- обычное дело после GreenPlan) молча обрежутся до первой тысячи
// без явного лимита.
//
// frustumCulled={false} -- не оптимизация, а исправление: three.js отсекает
// InstancedMesh по ограничивающей сфере, которую считает ОДИН раз, при
// первой отрисовке (Frustum.intersectsObject -> computeBoundingSphere, пока
// boundingSphere === null), а drei.Instances её потом не пересчитывает.
// Сфера остаётся вокруг тех экземпляров, что были в первом кадре, -- у
// объектов из DXF. Посадка GreenPlan в другой части участка при взгляде на неё
// отсекалась целиком: в газоне оставались вырезы под кусты, а самих кустов и
// деревьев не было (Харьковская, 1,4 км: камера над новыми кустами -- сфера
// вокруг старых вне кадра). Сфера на всех экземплярах вида всё равно покрыла
// бы весь участок, так что отсечение почти ничего не экономит.

import { Merged, useGLTF } from "@react-three/drei";
import { memo } from "react";
import type { ThreeEvent } from "@react-three/fiber";

export type InstancePlacement = {
  id: string;
  position: [number, number, number];
  rotation: number;
  scale: number;
};

// memo по (url, placements, onSelect): placements -- новый массив на каждый
// рендер PlacedObjects (собирается в useMemo, см. вызывающий код), поэтому
// сравнение по ссылке тут не сработает само по себе -- полагаемся на то, что
// сам useMemo вызывающего кода пересобирает массив только при реальном
// изменении objects/selectedId, а не на каждый чих (тот же принцип, что и у
// PlacedObjectItem).
export const InstancedVegetationGroup = memo(function InstancedVegetationGroup({
  url,
  placements,
  onSelect,
}: {
  url: string;
  placements: InstancePlacement[];
  onSelect: (id: string) => void;
}) {
  const { nodes } = useGLTF(url);

  return (
    <Merged meshes={nodes} limit={Math.max(placements.length, 1)} frustumCulled={false}>
      {(Model: Record<string, React.ComponentType<{ position?: [number, number, number]; rotation?: [number, number, number]; scale?: number; onClick?: (e: ThreeEvent<MouseEvent>) => void }>>) => {
        const parts = Object.values(Model);
        // Без обёртки-группы на размещение: клик на любую часть модели
        // (ствол/крона -- отдельные Instances-примитивы) обрабатывается сам
        // за себя через собственный onClick, группа сверху была бы лишним
        // Object3D на каждое из тысяч размещений без функциональной пользы.
        return (
          <>
            {placements.flatMap((p) =>
              parts.map((Part, i) => (
                <Part
                  key={`${p.id}-${i}`}
                  position={p.position}
                  rotation={[0, p.rotation, 0]}
                  scale={p.scale}
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
