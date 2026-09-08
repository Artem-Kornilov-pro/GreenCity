import type { Point2, RestrictionZone, SceneObject } from "../types";
import { setbackFor } from "../setbackNorms";
import { bufferOutward } from "./polygonBuffer";

// parser/parse_dxf.py отдаёт restriction-зону "building" как есть — контур
// самого здания, без визуального запаса. Реальный же запрет на посадку
// простирается за пределы стены (5м для дерева, 1.5м для кустарника — см.
// setbackNorms.ts), а этот отступ раньше был виден только в расчётах, не на
// сцене: рядом с домом либо ничего не подсвечивалось, либо казалось, что зона
// равна контуру дома. Тут эта область строится явно — outward-буфер контура
// здания на нужное расстояние (polygonBuffer.ts) — и рисуется как обычная
// RestrictionZone, чтобы не изобретать отдельный визуальный язык.

// Синтетические зоны — только для отображения. Их намеренно не подмешивают в
// зоны, по которым App.tsx/PlacedObjects.tsx считают нарушения через
// checkViolations() — та проверка уже сама применяет каталог норм к реальной
// зоне "building", добавление этих же колец туда задвоило бы предупреждения.
export function computeBuildingSetbackZones(objects: SceneObject[]): RestrictionZone[] {
  const treeDistance = setbackFor("building", "tree", 5.0);
  const bushDistance = setbackFor("building", "bush", 1.5);
  const zones: RestrictionZone[] = [];

  for (const obj of objects) {
    if (obj.type !== "building") continue;
    const footprint = (obj.metadata.footprint as Point2[] | undefined) ?? [];
    const name = (obj.metadata.name as string | undefined) ?? obj.id;

    const treeRing = bufferOutward(footprint, treeDistance);
    if (treeRing.length >= 3) {
      zones.push({
        id: `setback_tree_${obj.id}`,
        type: "building_setback_tree",
        name: `Отступ для дерева — ${name}`,
        polygon: treeRing,
        severity: "warning",
        minDistance: treeDistance,
        message: `До ${treeDistance} м от здания «${name}» деревья сажать нельзя (кустарник — можно, если дальше ${bushDistance} м от стены)`,
      });
    }

    const bushRing = bufferOutward(footprint, bushDistance);
    if (bushRing.length >= 3) {
      zones.push({
        id: `setback_bush_${obj.id}`,
        type: "building_setback_bush",
        name: `Отступ для кустарника — ${name}`,
        polygon: bushRing,
        severity: "forbidden",
        minDistance: bushDistance,
        message: `До ${bushDistance} м от здания «${name}» нельзя сажать ни дерево, ни кустарник`,
      });
    }
  }

  return zones;
}
