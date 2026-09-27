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
  // id исходного чертежа на сервере (backend/exchange/source_store.py):
  // экспорт DXF дописывает слои результата в него.
  sourceId?: string | null;
}

// Грань фасадного элемента (окно, козырёк) — 4 вершины в 3D, уже поднятые на
// нужную высоту. Не самостоятельные объекты сцены (их сотни-тысячи и они не
// перетаскиваются), рисуются напрямую как геометрия на фасаде здания.
export type FacadeQuad = Point3[];

// Бордюр — ломаная линия по земле (2D, без высоты). Тот же принцип, что у
// FacadeQuad: не самостоятельный объект, схематичная ribbon-геометрия.
export type CurbPolyline = Point2[];

// Газон GreenPlan (backend/greenplan/lawn.py) -- площадь, а не объекты:
// полигон с дырками (клумбы кустарника), в м². new -- устройство газона на
// открытой земле (идёт в ведомость), existing -- сохраняемый газон исходного плана.
export interface LawnArea {
  id: string;
  polygon: Point2[];
  holes: Point2[][];
  area_sqm: number;
  status: "new" | "existing";
  kind: string;
}

export interface Scene {
  boundary: Boundary | null;
  restrictions: RestrictionZone[];
  objects: SceneObject[];
  windows: FacadeQuad[];
  canopies: FacadeQuad[];
  curbs: CurbPolyline[];
  // Кольца нормативных отступов вокруг зданий, считает backend
  // (building_setbacks.py) -- только для отображения, в проверку нарушений не
  // идут: она применяет нормы к зоне "building" из restrictions сама.
  // Опционально -- у сцен, сохранённых до появления поля, его нет.
  buildingSetbacks?: RestrictionZone[];
  // Заполняет только GreenPlan; у сцен из /api/parse и сохранённых до
  // появления газона поля нет.
  lawns?: LawnArea[];
  meta: SceneMeta;
  // Заполняется только /api/parse-dwg (issue #50) -- файлы из загруженной
  // папки .dwg, которые не удалось сконвертировать (LibreDWG не всё умеет,
  // см. backend/exchange/dwg_batch_converter.py). Отсутствует у сцен из /api/parse.
  dwgConversionWarnings?: { file: string; error: string }[];
}
