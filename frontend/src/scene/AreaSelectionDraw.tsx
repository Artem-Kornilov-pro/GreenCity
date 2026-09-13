import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Line } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import type { Point2 } from "../types";

// Точки трассы лассо копятся, только если сдвиг от последней хотя бы такой --
// иначе на медленной обводке набирались бы тысячи почти совпадающих точек.
const MIN_POINT_SPACING_M = 0.4;
// Ниже этого числа точек или размаха обводки -- это случайный клик, а не
// осознанное выделение; полигон не создаётся.
const MIN_POINTS = 3;
const MIN_SPAN_M = 1;

// Слушатели вешаются на window, а не на объект сцены под курсором: R3F отдаёт
// pointermove/pointerup только тому объекту, над которым курсор в данный
// момент -- при быстром движении или уходе курсора за пределы канвы (что при
// выделении крупного или повёрнутого участка происходит постоянно) события
// терялись, и обводка навсегда зависала в "тянущемся" состоянии. Пересчёт
// точки на земле (y=0) через THREE.Raycaster вручную по каждому window-событию
// работает независимо от того, что физически находится под курсором.
export function AreaSelectionDraw({
  active,
  onComplete,
}: {
  active: boolean;
  onComplete: (polygon: Point2[]) => void;
}) {
  const { camera, gl } = useThree();
  const [points, setPoints] = useState<[number, number][]>([]);
  const dragging = useRef(false);
  const groundPlane = useMemo(() => new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), []);
  const raycaster = useMemo(() => new THREE.Raycaster(), []);

  useEffect(() => {
    if (!active) return;
    const canvas = gl.domElement;

    const groundPoint = (clientX: number, clientY: number): [number, number] | null => {
      const rect = canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1
      );
      raycaster.setFromCamera(ndc, camera);
      const hit = new THREE.Vector3();
      return raycaster.ray.intersectPlane(groundPlane, hit) ? [hit.x, hit.z] : null;
    };

    const finish = () => {
      if (dragging.current) {
        dragging.current = false;
        setPoints((current) => {
          if (current.length >= MIN_POINTS) {
            const xs = current.map((p) => p[0]);
            const zs = current.map((p) => p[1]);
            const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs));
            if (span >= MIN_SPAN_M) {
              onComplete(current.map(([x, z]) => ({ x, z })));
            }
          }
          return [];
        });
      }
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", finish);
    };

    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      const p = groundPoint(e.clientX, e.clientY);
      if (!p) return;
      setPoints((current) => {
        const last = current[current.length - 1];
        if (last && Math.hypot(p[0] - last[0], p[1] - last[1]) < MIN_POINT_SPACING_M) return current;
        return [...current, p];
      });
    };

    const onDown = (e: PointerEvent) => {
      e.stopPropagation();
      const p = groundPoint(e.clientX, e.clientY);
      if (!p) return;
      dragging.current = true;
      setPoints([p]);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", finish);
    };

    // capture: true -- перехватить раньше, чем событие дойдёт до собственной
    // обработки R3F (иначе клик по существующему дереву/кусту одновременно с
    // началом обводки выделил бы ещё и его).
    canvas.addEventListener("pointerdown", onDown, true);
    return () => {
      canvas.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", finish);
      dragging.current = false;
      setPoints([]);
    };
  }, [active, camera, gl, groundPlane, raycaster, onComplete]);

  if (!active || points.length < 2) return null;

  const previewPoints: [number, number, number][] = [
    ...points.map((p): [number, number, number] => [p[0], 0.06, p[1]]),
    [points[0][0], 0.06, points[0][1]],
  ];

  return <Line points={previewPoints} color="#2b6cff" lineWidth={2} />;
}
