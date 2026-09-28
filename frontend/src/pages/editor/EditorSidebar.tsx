import { LassoSelect, Move, Plus, RotateCw, Trash2, Trees, X } from "lucide-react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import { CATEGORY_LABELS, objectTypeLabel, type CatalogCategory, type CatalogItem } from "../../catalog";
import type { ZoneViolation } from "../../geometry";
import type { TransformMode } from "../../scene/PlacedObjects";
import type { RestrictionZone, SceneObject } from "../../types";
import { UploadDwgFolderButton, UploadDxfButton } from "./UploadButtons";

// Карточки почти белые: сквозь прозрачное стекло просвечивала сцена, и на
// зелёном газоне или тёмных зданиях текст карточек было не прочитать.
const CARD = "border-ink-200/60 bg-white/90 p-4 shadow-soft backdrop-blur-md";

const STEPS = [
  ["Загрузите чертёж", "границы, здания, сети и посадки распознаются сами"],
  ["Запустите GreenPlan", "озеленение по нормам отступов и проектам-аналогам"],
  ["Доработайте и выгрузите", "вручную или текстом через Ассистента, затем DXF и пояснительная записка"],
] as const;

// Пока сцены нет: с чего начать -- вместо пустой колонки.
function StartCard({
  onDxfFile,
  onDwgFolder,
  dxfLoading,
  dwgLoading,
}: {
  onDxfFile: (file: File) => void;
  onDwgFolder: (files: File[]) => void;
  dxfLoading: boolean;
  dwgLoading: boolean;
}) {
  const busy = dxfLoading || dwgLoading;
  return (
    <Card className={CARD}>
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-100 text-brand-700">
          <Trees className="h-5 w-5" />
        </span>
        <h3 className="text-base font-semibold text-ink-900">Начните с чертежа</h3>
      </div>
      <p className="mt-3 text-sm text-ink-500">DXF-файл или папка проекта с DWG. Их можно просто перетащить в окно.</p>
      <div className="mt-4 flex flex-col gap-2">
        <UploadDxfButton className="block" size="lg" variant="primary" loading={dxfLoading} disabled={busy} onFile={onDxfFile} />
        <UploadDwgFolderButton className="block" size="lg" loading={dwgLoading} disabled={busy} onFiles={onDwgFolder} />
      </div>
      <ol className="mt-5 flex flex-col gap-3">
        {STEPS.map(([title, hint], i) => (
          <li key={title} className="flex gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink-100 text-xs font-semibold text-ink-600">{i + 1}</span>
            <span className="text-sm">
              <span className="font-medium text-ink-800">{title}</span>
              <span className="block text-xs text-ink-500">{hint}</span>
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

// Левая панель плавает поверх сцены (как и топбар): сама колонка прозрачная,
// сцена видна между карточками.
export function EditorSidebar({
  sceneLoaded,
  onDxfFile,
  onDwgFolder,
  dxfLoading,
  dwgLoading,
  catalog,
  filteredCatalog,
  availableModelsCount,
  catalogFilter,
  onCatalogFilterChange,
  selectedItemId,
  onSelectItem,
  selectedItem,
  onAddObject,
  selectedArea,
  onClearSelection,
  hoveredZone,
  selectedObject,
  selectedObjectName,
  selectedViolations,
  transformMode,
  onTransformModeChange,
  onDelete,
}: {
  sceneLoaded: boolean;
  onDxfFile: (file: File) => void;
  onDwgFolder: (files: File[]) => void;
  dxfLoading: boolean;
  dwgLoading: boolean;
  catalog: CatalogItem[];
  filteredCatalog: CatalogItem[];
  availableModelsCount: number;
  catalogFilter: string;
  onCatalogFilterChange: (value: string) => void;
  selectedItemId: string;
  onSelectItem: (id: string) => void;
  selectedItem: CatalogItem | undefined;
  onAddObject: (item: CatalogItem) => void;
  selectedArea: RestrictionZone | null;
  onClearSelection: () => void;
  hoveredZone: RestrictionZone | null;
  selectedObject: SceneObject | null;
  selectedObjectName: string | null;
  selectedViolations: ZoneViolation[];
  transformMode: TransformMode;
  onTransformModeChange: (mode: TransformMode) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="scrollbar-thin absolute left-0 top-0 z-20 flex h-full w-80 flex-col gap-3 overflow-y-auto border-r border-white/30 bg-white/25 px-3 pb-3 pt-16 shadow-sm backdrop-blur-2xl backdrop-saturate-150">
      {!sceneLoaded && <StartCard onDxfFile={onDxfFile} onDwgFolder={onDwgFolder} dxfLoading={dxfLoading} dwgLoading={dwgLoading} />}

      {sceneLoaded && (
        <Card className={CARD}>
          <h3 className="text-sm font-semibold text-ink-900">Добавить объект</h3>
          <Input
            className="mt-3"
            placeholder="Поиск: раскидистое, низкое, сосна…"
            value={catalogFilter}
            onChange={(e) => onCatalogFilterChange(e.target.value)}
          />
          <div className="mt-2 flex flex-col gap-2">
            <select
              className="h-10 w-full rounded-xl border border-ink-200 bg-white px-3 text-sm text-ink-900 focus:outline-none focus:ring-2 focus:ring-brand-400"
              value={selectedItemId}
              onChange={(e) => onSelectItem(e.target.value)}
            >
              {(Object.keys(CATEGORY_LABELS) as CatalogCategory[]).map((category) => {
                const items = filteredCatalog.filter((i) => i.category === category);
                if (items.length === 0) return null;
                return (
                  <optgroup key={category} label={CATEGORY_LABELS[category]}>
                    {items.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
            <Button className="w-full" disabled={!selectedItem} onClick={() => selectedItem && onAddObject(selectedItem)}>
              <Plus className="h-4 w-4" />
              Добавить на план
            </Button>
          </div>
          <p className="mt-2 text-xs text-ink-500">
            {catalogFilter ? `${filteredCatalog.length} из ${catalog.length}` : `${catalog.length} видов`} в каталоге
            {availableModelsCount > 0 ? `, 3D-моделей: ${availableModelsCount}` : ", модели не подключены — рисуются заглушки"}
          </p>
        </Card>
      )}

      {selectedArea && (
        <Card className={CARD}>
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
              <LassoSelect className="h-3.5 w-3.5 text-success-500" />
              {selectedArea.name}
            </h3>
            <Button size="icon" variant="ghost" title="Снять выделение" onClick={onClearSelection}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
          <p className="mt-1 text-xs text-ink-500">Можно сослаться на неё в правке текстом: «посади здесь кусты», «убери отсюда лавки».</p>
        </Card>
      )}

      {hoveredZone && (
        <Card className={CARD}>
          <h3 className="text-sm font-semibold text-ink-900">{hoveredZone.name}</h3>
          <p className="mt-1 text-sm text-ink-500">{hoveredZone.message}</p>
          <p className="mt-2 text-xs text-ink-500">
            severity: {hoveredZone.severity}, minDistance: {hoveredZone.minDistance} м
          </p>
        </Card>
      )}

      {selectedObject && (
        <Card className={CARD}>
          <h3 className="text-sm font-semibold text-ink-900">{selectedObjectName}</h3>
          <p className="mt-0.5 truncate text-xs text-ink-500" title={selectedObject.id}>
            {objectTypeLabel(selectedObject.type)} · {selectedObject.id}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            x={selectedObject.position.x.toFixed(2)} z={selectedObject.position.z.toFixed(2)}, поворот=
            {((selectedObject.rotation * 180) / Math.PI).toFixed(0)}°
          </p>
          <div className="mt-3 flex gap-2">
            <Button className="flex-1" variant={transformMode === "translate" ? "primary" : "outline"} onClick={() => onTransformModeChange("translate")}>
              <Move className="h-4 w-4" />
              Двигать
            </Button>
            <Button className="flex-1" variant={transformMode === "rotate" ? "primary" : "outline"} onClick={() => onTransformModeChange("rotate")}>
              <RotateCw className="h-4 w-4" />
              Вращать
            </Button>
          </div>
          {selectedViolations.length > 0 ? (
            <div className="mt-3 flex flex-col gap-1.5">
              {selectedViolations.map((v) => (
                <Badge key={v.zone.id} variant="warning" className="justify-start">
                  ⚠ {v.zone.message} (мин. {v.minDistance} м)
                </Badge>
              ))}
            </div>
          ) : (
            <Badge variant="success" className="mt-3">
              Нарушений отступов нет
            </Badge>
          )}
          <Button variant="danger" className="mt-3 w-full" onClick={() => onDelete(selectedObject.id)}>
            <Trash2 className="h-4 w-4" />
            Удалить (Delete)
          </Button>
        </Card>
      )}

      {sceneLoaded && (
        <Card className={`${CARD} text-xs text-ink-500`}>
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-danger-500" /> запрещено
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-warning-500" /> предупреждение
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-success-500" /> разрешено
            </div>
          </div>
          <p className="mt-3 leading-relaxed text-ink-500">
            Клик по объекту — выбрать. «Двигать» — тащить по земле, «Вращать» — вокруг своей оси. Delete/Backspace — удалить выбранный
            объект.
          </p>
        </Card>
      )}
    </aside>
  );
}
