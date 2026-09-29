import * as THREE from "three";
import type { Point2 } from "../types";

// THREE.Shape строится в плоскости XY из (x, -z) и поворачивается на -90°
// вокруг X: так ось выдавливания совпадает с вертикалью сцены.
export function flatPolygonGeometry(points: Point2[]): THREE.BufferGeometry | null {
  if (points.length < 3) return null;
  const shape = new THREE.Shape(points.map((p) => new THREE.Vector2(p.x, -p.z)));
  const geo = new THREE.ShapeGeometry(shape);
  geo.rotateX(-Math.PI / 2);
  return geo;
}

export function extrudedPolygonGeometry(points: Point2[], height: number): THREE.BufferGeometry | null {
  if (points.length < 3) return null;
  const shape = new THREE.Shape(points.map((p) => new THREE.Vector2(p.x, -p.z)));
  const geo = new THREE.ExtrudeGeometry(shape, { depth: Math.max(height, 0.1), bevelEnabled: false });
  geo.rotateX(-Math.PI / 2);
  return geo;
}

// Плоские слои (земля окрестностей -5, земля участка -4, газон -3, сетка -2,
// зоны -- после непрозрачного)
// рисуются строго по renderOrder и не пишут глубину: на крупной сцене
// точности буфера глубины не хватает, и слои мерцали. Здания, деревья и
// бордюры пишут глубину как обычно.
export const FLAT_LAYER_ORDER = { surroundings: -5, ground: -4, lawn: -3, grid: -2 } as const;

// Высота загруженной модели (по габариту всего графа glTF), с кешем на
// объект: модель одна на вид, а спрашивают её для тысяч размещений.
const modelHeightCache = new WeakMap<THREE.Object3D, number>();

function modelHeight(object: THREE.Object3D): number {
  let height = modelHeightCache.get(object);
  if (height === undefined) {
    const box = new THREE.Box3().setFromObject(object);
    height = box.isEmpty() ? 0 : box.max.y - box.min.y;
    modelHeightCache.set(object, height);
  }
  return height;
}

// Во сколько раз увеличить модель, чтобы она встала в высоту из каталога
// (реальная высота вида). Нет высоты -- модель как есть.
export function modelFitScale(object: THREE.Object3D, targetHeight: number | undefined): number {
  if (!targetHeight) return 1;
  const height = modelHeight(object);
  return height > 0 ? targetHeight / height : 1;
}
