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

// Грань фасадного элемента (окно, козырёк) — 4 вершины в 3D, уже поднятые на
// нужную высоту. Не самостоятельные объекты сцены (их сотни-тысячи и они не
// перетаскиваются), рисуются напрямую как геометрия на фасаде здания.
export type FacadeQuad = Point3[];

export interface Scene {
  boundary: Boundary | null;
  restrictions: RestrictionZone[];
  objects: SceneObject[];
  windows: FacadeQuad[];
  canopies: FacadeQuad[];
  meta: SceneMeta;
}

// Типы объектов, которые пользователь может перетаскивать на сцене.
// path_segment/hedge_segment — линейные элементы дизайна (плитка дорожки,
// секция живой изгороди), которые собираются в дорожку/изгородь вручную:
// пользователь ставит и разворачивает несколько сегментов подряд, а не
// рисует полилинию — так они укладываются в ту же модель SceneObject
// (позиция + поворот + масштаб), что и все остальные объекты.
export const EDITABLE_TYPES = new Set([
  "tree",
  "bush",
  "bench",
  "lamp",
  "trash",
  "fountain",
  "path_segment",
  "hedge_segment",
]);
