import { useEffect, useRef } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneBounds } from "../geometry";

type OrbitLike = { target: THREE.Vector3; update: () => void } | null;

// Позиционирует камеру и цель OrbitControls так, чтобы вся сцена помещалась в
// кадр, и расширяет near/far под её реальный масштаб (локация 5 — авеню на
// ~4км, дефолтные far=1000 у PerspectiveCamera обрезали бы половину сцены).
// Срабатывает только на смену `sceneLoadToken` (= загрузили НОВУЮ сцену), не
// на каждый пересчёт `bounds` -- тот меняет ссылку на каждый рендер сцены
// (включая перетаскивание объекта), и завязка эффекта на него дёргала бы
// камеру на каждое такое действие.
//
// Раньше признаком "новая сцена" служил сам `scene.boundary`, сравниваемый
// через ===. Ломалось на реальных DWG-проектах без слоя границы (issue #50
// follow-up): boundary у ДВУХ РАЗНЫХ таких сцен подряд одинаково `null`, а
// `null === null` в JS -- эффект принимал вторую, третью и т.д. сцену без
// границы за "ту же самую" и молча не перецентровывал камеру, оставляя её
// там, где она была для самой первой (реальный репорт: "камера не по центру
// проекта"). `sceneLoadToken` -- явный монотонный счётчик от родителя,
// инкрементируется только при ЗАГРУЗКЕ новой сцены (файл/DWG-папка/проект),
// не при её редактировании -- не подвержен этой коллизии в принципе.
//
// Второй, более глубокий баг той же природы: в SceneView.tsx <FitCamera>
// стоит в JSX ПЕРЕД <OrbitControls makeDefault>, а React выполняет эффекты
// сиблингов в порядке их объявления -- значит на первом монтировании эффект
// FitCamera срабатывает РАНЬШЕ, чем OrbitControls успевает зарегистрировать
// себя в сторе r3f (useThree().controls), и `controls` в этот момент `null`.
// Раньше ref "уже применили для этого token" помечался ДО проверки `controls`
// -- camera.position выставлялась верно, а controls.target -- нет, и
// OrbitControls продолжал вращаться вокруг СВОЕГО прежнего/дефолтного
// target (для реальных DWG-сцен это буквально другая точка в километрах от
// содержимого, см. issue #50 follow-up) -- эффект НЕ повторялся повторно,
// когда controls наконец появлялся (хотя он в зависимостях и реально меняет
// ссылку), потому что ref уже был "занят". Теперь ref фиксируется только
// ПОСЛЕ успешного controls.target.set -- пока controls ещё null, эффект не
// считается выполненным и сработает снова на следующий рендер, когда
// controls появится.
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
      // Фиксируем "готово" только теперь -- см. комментарий выше.
      fittedTokenRef.current = sceneLoadToken;
    }
  }, [sceneLoadToken, bounds, camera, controls]);

  return null;
}
