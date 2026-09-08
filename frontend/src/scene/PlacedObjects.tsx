import { useRef, type RefObject } from "react";
import * as THREE from "three";
import { TransformControls } from "@react-three/drei";
import type { RestrictionZone, SceneObject } from "../types";
import { EDITABLE_TYPES } from "../types";
import { checkViolations } from "../geometry";
import { plantKindOfObjectType } from "../setbackNorms";
import { ObjectVisual } from "./ObjectVisual";

export type TransformMode = "translate" | "rotate";

export function PlacedObjects({
  objects,
  restrictions,
  selectedId,
  transformMode,
  onSelect,
  onMove,
  onRotate,
}: {
  objects: SceneObject[];
  restrictions: RestrictionZone[];
  selectedId: string | null;
  transformMode: TransformMode;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
}) {
  const items = objects.filter((o) => o.type !== "building");
  return (
    <>
      {items.map((obj) => {
        // Подъезд встроен в стену по построению — отступ от здания к нему
        // неприменим, проверять и подсвечивать это как нарушение бессмысленно.
        const violated =
          obj.type !== "entrance" &&
          checkViolations(obj.position.x, obj.position.z, restrictions, plantKindOfObjectType(obj.type)).length > 0;
        return (
          <PlacedObjectItem
            key={obj.id}
            obj={obj}
            violated={violated}
            selected={obj.id === selectedId}
            transformMode={transformMode}
            onSelect={onSelect}
            onMove={onMove}
            onRotate={onRotate}
          />
        );
      })}
    </>
  );
}

function PlacedObjectItem({
  obj,
  violated,
  selected,
  transformMode,
  onSelect,
  onMove,
  onRotate,
}: {
  obj: SceneObject;
  violated: boolean;
  selected: boolean;
  transformMode: TransformMode;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const editable = EDITABLE_TYPES.has(obj.type);

  const body = (
    <group
      ref={groupRef}
      position={[obj.position.x, obj.position.y, obj.position.z]}
      rotation={[0, obj.rotation, 0]}
      scale={obj.scale}
      onClick={(e) => {
        e.stopPropagation();
        if (editable) onSelect(obj.id);
      }}
    >
      <ObjectVisual type={obj.type} violated={violated} metadata={obj.metadata} />
    </group>
  );

  if (!selected || !editable) return body;

  // Двигать — только по земле (Y выключен). Поворачивать — только вокруг
  // вертикальной оси (X/Z выключены), иначе объект завалится набок.
  const isTranslate = transformMode === "translate";

  return (
    <TransformControls
      object={groupRef as RefObject<THREE.Object3D>}
      mode={transformMode}
      showX={isTranslate}
      showY={!isTranslate}
      showZ={isTranslate}
      onMouseUp={() => {
        const g = groupRef.current;
        if (!g) return;
        if (isTranslate) {
          onMove(obj.id, g.position.x, g.position.z);
        } else {
          onRotate(obj.id, g.rotation.y);
        }
      }}
    >
      {body}
    </TransformControls>
  );
}
