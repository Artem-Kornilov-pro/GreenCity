// Клиент каталога посадок/МАФ. Каталог живёт на бэкенде (backend/plant_catalog.py)
// и там же является единым источником правды -- фронтенд его только читает:
// строит по нему панель "Добавить объект" и решает, чем рисовать объект.

import type { SceneObject } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export type CatalogCategory = "tree" | "bush" | "groundcover" | "paving" | "furniture";

export type RenderShape =
  | "cone"
  | "cluster"
  | "pine"
  | "sphere"
  | "box"
  | "patch"
  | "bench"
  | "lamp"
  | "trash"
  | "fountain";

export interface CatalogItem {
  id: string;
  category: CatalogCategory;
  label: string;
  setback_kind: "tree" | "bush" | null;
  object_type: string;
  model: string;
  dimensions: {
    height: number;
    radius: number | null;
    trunk_height: number | null;
    width: number | null;
    depth: number | null;
  };
  render: { shape: RenderShape; color: string };
}

export const CATEGORY_LABELS: Record<CatalogCategory, string> = {
  tree: "Деревья",
  bush: "Кустарники",
  groundcover: "Травяные покрытия",
  paving: "Мощение",
  furniture: "Малые формы",
};

export async function fetchCatalog(): Promise<CatalogItem[]> {
  const res = await fetch(`${API_BASE}/api/catalog`);
  if (!res.ok) throw new Error(`Не удалось загрузить каталог (${res.status})`);
  return res.json() as Promise<CatalogItem[]>;
}

// Манифест реально имеющихся .glb -- его пишет скрипт конвертации моделей
// (tools/convert_models.mjs). Нет манифеста -- значит моделей ещё не положили,
// и всё рисуется примитивами-заглушками; это штатный режим, не ошибка.
//
// try/catch тут обязателен и проверку res.ok не заменяет: dev-сервер Vite на
// отсутствующий файл отдаёт не 404, а 200 с index.html (SPA-фолбэк), и ловится
// это только на разборе JSON.
export async function fetchModelManifest(): Promise<Set<string>> {
  try {
    const res = await fetch("/models/manifest.json");
    if (!res.ok) return new Set();
    const data = (await res.json()) as { models?: string[] };
    return new Set(data.models ?? []);
  } catch {
    return new Set();
  }
}

// Какие типы объектов сцены пользователь может создавать/двигать. Берётся из
// каталога, а не хардкодится: добавили запись в plant_catalog.py -- тип сразу
// стал доступен и в панели добавления, и для перетаскивания.
export function editableTypesFrom(catalog: CatalogItem[]): Set<string> {
  return new Set(catalog.map((i) => i.object_type));
}

// Дефолтная позиция в каталоге для объектов, пришедших из DXF или от
// генератора -- у них нет metadata.catalogId, но нарисовать их надо.
const DEFAULT_ITEM_BY_TYPE: Record<string, string> = {
  tree: "tree_medium",
  bush: "bush_medium",
  bench: "bench",
  lamp: "lamp",
  trash: "trash",
  fountain: "fountain",
  path_segment: "path_segment",
  hedge_segment: "hedge_segment",
  lawn_patch: "lawn_patch",
  flowerbed_patch: "flowerbed_patch",
};

// Стабильный хеш строки: нужен, чтобы у объекта без явного вида модель была
// всегда одна и та же (не менялась на каждый рендер), но у разных объектов --
// разная.
function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function resolveCatalogItem(
  obj: SceneObject,
  byId: Map<string, CatalogItem>,
  availableModels?: Set<string>
): CatalogItem | undefined {
  const explicit = obj.metadata?.catalogId;
  if (typeof explicit === "string" && byId.has(explicit)) return byId.get(explicit);

  // Совместимость с объектами, созданными до появления каталога: там вид
  // хранился в metadata.treeKind/bushKind ("medium", "pine", ...).
  const legacyKind = obj.metadata?.treeKind ?? obj.metadata?.bushKind;
  if (typeof legacyKind === "string") {
    const legacyId = `${obj.type}_${legacyKind}`;
    if (byId.has(legacyId)) return byId.get(legacyId);
  }

  // Деревья из DXF-подосновы и от автогенератора приходят без вида. Если
  // подключен пак моделей -- раздаём им РАЗНЫЕ модели вместо одной и той же:
  // ради этого пак и подключался, иначе весь двор зарастает клонами.
  if (availableModels && availableModels.size > 0) {
    const category: CatalogCategory | null =
      obj.type === "tree" ? "tree" : obj.type === "bush" ? "bush" : null;
    if (category) {
      const withModels = [...byId.values()].filter(
        (i) => i.category === category && availableModels.has(i.model)
      );
      if (withModels.length > 0) {
        return withModels[hashString(obj.id) % withModels.length];
      }
    }
  }

  return byId.get(DEFAULT_ITEM_BY_TYPE[obj.type] ?? "");
}
