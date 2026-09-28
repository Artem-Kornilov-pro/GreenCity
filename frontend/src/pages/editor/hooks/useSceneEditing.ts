import { useCallback, useEffect, useMemo, useState } from "react";
import type { CatalogItem } from "../../../catalog";
import { buildZoneIndex, checkViolationsAt, computeSceneBounds } from "../../../geometry";
import type { TransformMode } from "../../../scene/PlacedObjects";
import { plantKindOfObjectType } from "../../../setbackNorms";
import type { Point2, RestrictionZone, Scene, SceneObject } from "../../../types";
import { makeId, SELECTION_ZONE_NAME, SELECTION_ZONE_TYPE } from "../editorTypes";

type ObjectPatch = Partial<Pick<SceneObject, "position" | "rotation">>;

function patchObject(scene: Scene, id: string, patch: (o: SceneObject) => ObjectPatch): Scene {
  return { ...scene, objects: scene.objects.map((o) => (o.id === id ? { ...o, ...patch(o) } : o)) };
}

// Сцена редактора и всё, что меняет её вручную: выбор объекта, перемещение,
// поворот, удаление, добавление из каталога, выделение зоны мышкой.
export function useSceneEditing() {
  const [scene, setScene] = useState<Scene | null>(null);
  // Растёт при загрузке новой сцены (файл, папка, проект) -- сигнал FitCamera
  // перецентровать камеру. Правки текущей сцены счётчик не трогают.
  const [sceneLoadToken, setSceneLoadToken] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [transformMode, setTransformMode] = useState<TransformMode>("translate");
  const [selectionMode, setSelectionMode] = useState(false);

  // Новая сцена целиком -- камера перецентрируется, выбор сбрасывается.
  const loadScene = useCallback((next: Scene) => {
    setScene(next);
    setSceneLoadToken((t) => t + 1);
    setSelectedId(null);
  }, []);

  // Та же сцена после правки (ассистент, GreenPlan, отмена): выбор остаётся,
  // если выбранный объект в ней ещё есть.
  const replaceScene = useCallback((next: Scene) => {
    setScene(next);
    setSelectedId((prev) => (prev && next.objects.some((o) => o.id === prev) ? prev : null));
  }, []);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    setTransformMode("translate");
  }, []);

  const move = useCallback((id: string, x: number, z: number) => {
    setScene((prev) => (prev ? patchObject(prev, id, (o) => ({ position: { ...o.position, x, z } })) : prev));
  }, []);

  const rotate = useCallback((id: string, rotation: number) => {
    setScene((prev) => (prev ? patchObject(prev, id, () => ({ rotation })) : prev));
  }, []);

  const remove = useCallback((id: string) => {
    setScene((prev) => (prev ? { ...prev, objects: prev.objects.filter((o) => o.id !== id) } : prev));
    setSelectedId((prev) => (prev === id ? null : prev));
  }, []);

  // Новый объект -- в центре участка, следующие того же типа -- сеткой рядом,
  // чтобы не легли друг на друга.
  const addObject = useCallback((item: CatalogItem) => {
    setScene((prev) => {
      if (!prev) return prev;
      const bounds = computeSceneBounds(prev);
      const sameTypeCount = prev.objects.filter((o) => o.type === item.object_type).length;
      const newObject: SceneObject = {
        id: `${item.object_type}_manual_${makeId().slice(0, 8)}`,
        type: item.object_type,
        model: item.model,
        position: {
          x: (bounds.minX + bounds.maxX) / 2 + (sameTypeCount % 5) * 2.5,
          y: 0,
          z: (bounds.minZ + bounds.maxZ) / 2 + Math.floor(sameTypeCount / 5) * 2.5,
        },
        rotation: 0,
        scale: 1,
        // species -- для правил отступа по породе (липе 10 м от здания);
        // source: "manual" -- при экспорте объект идёт на слой USER_*.
        metadata: { catalogId: item.id, label: item.label, source: "manual", ...(item.setback_kind ? { species: item.label } : {}) },
      };
      setSelectedId(newObject.id);
      setTransformMode("translate");
      return { ...prev, objects: [...prev.objects, newObject] };
    });
  }, []);

  // Одно активное выделение за раз -- новое заменяет предыдущее.
  const selectArea = useCallback((polygon: Point2[]) => {
    setScene((prev) => {
      if (!prev) return prev;
      const zone: RestrictionZone = {
        id: `selection_${makeId()}`,
        type: SELECTION_ZONE_TYPE,
        name: SELECTION_ZONE_NAME,
        polygon,
        severity: "allowed",
        minDistance: 0,
        message: "Зона, выделенная вручную для правки текстом",
      };
      return { ...prev, restrictions: [...prev.restrictions.filter((z) => z.type !== SELECTION_ZONE_TYPE), zone] };
    });
    setSelectionMode(false);
  }, []);

  const clearArea = useCallback(() => {
    setScene((prev) => (prev ? { ...prev, restrictions: prev.restrictions.filter((z) => z.type !== SELECTION_ZONE_TYPE) } : prev));
  }, []);

  // Delete/Backspace удаляет выбранный объект, если фокус не в поле ввода.
  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        remove(selectedId);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, remove]);

  const selectedArea = scene?.restrictions.find((z) => z.type === SELECTION_ZONE_TYPE) ?? null;
  // Индекс зон перестраивается только при смене разметки участка, а не на
  // каждое перемещение объекта.
  const zoneIndex = useMemo(() => buildZoneIndex(scene?.restrictions ?? []), [scene?.restrictions]);
  const selectedObject = useMemo(() => scene?.objects.find((o) => o.id === selectedId) ?? null, [scene?.objects, selectedId]);
  const selectedViolations = useMemo(
    () =>
      selectedObject
        ? checkViolationsAt(
            selectedObject.position.x,
            selectedObject.position.z,
            zoneIndex,
            plantKindOfObjectType(selectedObject.type),
            typeof selectedObject.metadata.species === "string" ? selectedObject.metadata.species : undefined,
          )
        : [],
    [selectedObject, zoneIndex],
  );

  return {
    scene,
    sceneLoadToken,
    loadScene,
    replaceScene,
    selectedId,
    select,
    selectedObject,
    selectedViolations,
    transformMode,
    setTransformMode,
    selectionMode,
    setSelectionMode,
    selectedArea,
    move,
    rotate,
    remove,
    addObject,
    selectArea,
    clearArea,
  };
}
