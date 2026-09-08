// Схема соответствует JSON, который отдаёт backend/main.py (обёртка над
// parser/parse_dxf.py). Меняется в одном месте — там же, где парсер.

export interface Point2 {
  x: number;
  z: number;
}

export interface Point3 {
  x: number;
  y: number;
  z: number;
}

export interface Boundary {
  polygon: Point2[];
  sourceLayer: string;
}

export type Severity = "forbidden" | "warning" | "allowed";

export interface RestrictionZone {
  id: string;
  type: string;
  name: string;
  polygon: Point2[];
  severity: Severity;
  minDistance: number;
  message: string;
  maxHeight?: number;
}

export interface SceneObject {
  id: string;
  type: string;
  model: string;
  position: Point3;
  rotation: number;
  scale: number;
  metadata: Record<string, unknown>;
}

export interface SceneMeta {
  scale: number;
  insunits: number;
  origin: { x: number; y: number };
  buildingCount: number;
  pointObjectCount: number;
}

export interface Scene {
  boundary: Boundary | null;
  restrictions: RestrictionZone[];
  objects: SceneObject[];
  meta: SceneMeta;
}

// Типы объектов, которые пользователь может перетаскивать на сцене.
export const EDITABLE_TYPES = new Set(["tree", "bush", "bench", "lamp"]);
