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
  // Класс слоя, с которого взята зона (газон, парковка…), -- не зависит от
  // того, как слой назван в чертеже.
  layerKind?: string | null;
  // Класс напряжения воздушной ЛЭП, кВ: из имени слоя или параметров GreenPlan; нет -- 10 кВ.
  voltageKv?: number | null;
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
  // Что разбор взял из каждого слоя чертежа (окно «Слои чертежа»).
  layers?: LayerInfo[];
  // Назначения слоёв пользователем, с которыми разобран чертёж.
  layerMap?: Record<string, string>;
  // Что разбор сделал сам и что стоит проверить: единицы, граница, блоки.
  warnings?: string[];
  // Слоя границы нет: замкнутые контуры, из которых можно выбрать границу.
  boundaryCandidates?: BoundaryCandidate[];
  // Граница, выбранная пользователем, в координатах чертежа.
  boundaryOverride?: number[][] | null;
  // Привязка к карте (backend/core/geo.py): только у чертежей в МСК.
  geo?: SceneGeo | null;
}

export interface BoundaryCandidate {
  id: number;
  layer: string;
  area: number; // м²
  points: number[][]; // координаты чертежа
}

export interface SceneGeo {
  crs: string;
  assumed: boolean;
  center: { lat: number; lon: number };
  // x = a*lon + b*lat + c, z = d*lon + e*lat + f -- метры сцены.
  toScene: [number, number, number, number, number, number];
}

// Слой чертежа в сводке распознавания (parser/dxf_parsing/drawing.py).
// label = null -- слой не распознан, его содержимое не учтено.
export interface LayerInfo {
  name: string;
  // Имя без кракозябр, если в файле оно записано в другой кодировке.
  displayName?: string | null;
  entities: number;
  kind: string | null;
  label: string | null;
  source: "override" | "name" | "normalized" | "synonym" | "content" | null;
}

// Что можно назначить слою (GET /api/layer-kinds).
export interface LayerKind {
  id: string;
  label: string;
}

// Грань фасадного элемента (окно, козырёк) -- 4 вершины на нужной высоте;
// рисуется геометрией на фасаде, а не объектом сцены.
export type FacadeQuad = Point3[];

// Бордюр — ломаная линия по земле (2D, без высоты). Тот же принцип, что у
// FacadeQuad: не самостоятельный объект, схематичная ribbon-геометрия.
export type CurbPolyline = Point2[];

// Газон (backend/greenplan/lawn.py) -- площадь, а не объекты: полигон с
// дырками (клумбы кустарника), в м². existing -- сохраняемый газон исходного
// чертежа, приходит сразу при загрузке; new -- устройство газона на открытой
// земле от GreenPlan (идёт в ведомость).
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
  // Кольца нормативных отступов вокруг зданий -- только для отображения.
  // У сцен, сохранённых до появления поля, его нет.
  buildingSetbacks?: RestrictionZone[];
  // Заполняет только GreenPlan; у сцен из /api/parse и сохранённых до
  // появления газона поля нет.
  lawns?: LawnArea[];
  meta: SceneMeta;
  // Только у /api/parse-dwg: файлы .dwg, которые не удалось сконвертировать.
  dwgConversionWarnings?: { file: string; error: string }[];
}
