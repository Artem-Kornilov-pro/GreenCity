import { useEffect, useRef } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneBounds } from "../geometry";

type OrbitLike = { target: THREE.Vector3; update: () => void } | null;

// Ставит камеру и цель OrbitControls так, чтобы сцена целиком помещалась в
// кадр, и подгоняет near/far под масштаб сцены (улица на километры не
// влезла бы в far=1000).
//
// Срабатывает на смену sceneLoadToken -- счётчика загрузок новой сцены, а не
// на bounds (тот меняется при любой правке) и не на boundary (у двух сцен
// без границы он одинаково null).
//
// Эффект считается выполненным только после controls.target.set: на первом
// монтировании OrbitControls ещё не зарегистрирован (controls === null), и
// эффект должен сработать снова, когда он появится.
export function FitCamera({ bounds, sceneLoadToken }: { bounds: SceneBounds; sceneLoadToken: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as OrbitLike;
  const fittedTokenRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (fittedTokenRef.current === sceneLoadToken) return;

    const width = bounds.maxX - bounds.minX;
    const depth = bounds.maxZ - bounds.minZ;
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cz = (bounds.minZ + bounds.maxZ) / 2;
    const maxDim = Math.max(width, depth, 20);

    const camDist = maxDim * 0.75;
    // Камера из useThree() меняется императивно -- это штатный способ
    // управлять ею в react-three-fiber.
    // oxlint-disable-next-line react/immutability
    camera.near = Math.max(maxDim / 2000, 0.05);
    camera.far = maxDim * 6 + 1000;
    camera.position.set(cx - camDist * 0.6, camDist * 0.55 + bounds.maxHeight, cz + camDist * 0.6);
    camera.updateProjectionMatrix();

    if (controls) {
      controls.target.set(cx, bounds.maxHeight * 0.25, cz);
      controls.update();
      // Фиксируем "готово" только теперь -- см. комментарий выше.
      fittedTokenRef.current = sceneLoadToken;
    }
  }, [sceneLoadToken, bounds, camera, controls]);

  return null;
}
