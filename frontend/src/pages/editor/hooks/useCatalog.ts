import { useEffect, useMemo, useState } from "react";
import { fetchCatalog, fetchModelManifest, type CatalogItem } from "../../../catalog";
import { useAsyncTask } from "../../../hooks/useAsyncTask";

// Каталог посадок и МАФ (источник правды -- backend/core/plant_catalog.py) и
// выбор позиции в панели «Добавить объект».
export function useCatalog() {
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [availableModels, setAvailableModels] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [selectedItemId, setSelectedItemId] = useState("");

  const load = useAsyncTask(async () => {
    setCatalog(await fetchCatalog());
  });
  const { run: loadCatalog } = load;

  useEffect(() => {
    void loadCatalog();
    void fetchModelManifest().then(setAvailableModels);
  }, [loadCatalog]);

  const catalogById = useMemo(() => new Map(catalog.map((i) => [i.id, i])), [catalog]);
  const filteredCatalog = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? catalog.filter((i) => i.label.toLowerCase().includes(q)) : catalog;
  }, [catalog, filter]);
  // Выбранная позиция, если она прошла фильтр, иначе -- первая подходящая.
  const effectiveItemId = filteredCatalog.some((i) => i.id === selectedItemId) ? selectedItemId : (filteredCatalog[0]?.id ?? "");

  return {
    catalog,
    catalogById,
    availableModels,
    load,
    filter,
    setFilter,
    filteredCatalog,
    selectedItemId: effectiveItemId,
    setSelectedItemId,
    selectedItem: catalogById.get(effectiveItemId),
  };
}
