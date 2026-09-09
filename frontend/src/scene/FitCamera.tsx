import { useEffect, useRef } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { Boundary } from "../types";
import type { SceneBounds } from "../geometry";

type OrbitLike = { target: THREE.Vector3; update: () => void } | null;

// Позиционирует камеру и цель OrbitControls так, чтобы вся сцена помещалась в
// кадр, и расширяет near/far под её реальный масштаб (локация 5 — авеню на
// ~4км, дефолтные far=1000 у PerspectiveCamera обрезали бы половину сцены).
// Срабатывает только на смену `boundary` (= загрузили новую сцену): `bounds`
// пересчитывается в новый объект на каждый рендер, и завязка эффекта на него
// дёргала бы камеру при каждом перетаскивании объекта. Признак "сцена
// сменилась" держим явно в ref, а не прячем в урезанном списке зависимостей --
// так линтер видит все реальные зависимости, а поведение остаётся прежним.
export function FitCamera({ bounds, boundary }: { bounds: SceneBounds; boundary: Boundary | null }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as OrbitLike;
  const fittedBoundaryRef = useRef<Boundary | null | undefined>(undefined);

  useEffect(() => {
    if (fittedBoundaryRef.current === boundary) return;
    fittedBoundaryRef.current = boundary;

    const width = bounds.maxX - bounds.minX;
    const depth = bounds.maxZ - bounds.minZ;
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cz = (bounds.minZ + bounds.maxZ) / 2;
    const maxDim = Math.max(width, depth, 20);

    const camDist = maxDim * 0.75;
    // В react-three-fiber объект камеры из useThree() правится императивно --
    // это и есть штатный способ ей управлять, варианта "через state" не
    // существует, поэтому правило immutability здесь неприменимо.
    // oxlint-disable-next-line react/immutability
    camera.near = Math.max(maxDim / 2000, 0.05);
    camera.far = maxDim * 6 + 1000;
    camera.position.set(cx - camDist * 0.6, camDist * 0.55 + bounds.maxHeight, cz + camDist * 0.6);
    camera.updateProjectionMatrix();

    if (controls) {
      controls.target.set(cx, bounds.maxHeight * 0.25, cz);
      controls.update();
    }
  }, [boundary, bounds, camera, controls]);

  return null;
}
