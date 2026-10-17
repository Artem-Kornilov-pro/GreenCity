import { useCallback, useState } from "react";
import { getLayerKinds, reparseScene } from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import type { BoundaryCandidate, LayerKind, Scene } from "../../../types";

export interface LayerSummary {
  total: number;
  // Нераспознанные слои, самые крупные первыми. Доля сущностей тут не
  // показательна: тысячи граней окон заглушили бы слой со всеми зданиями.
  unrecognized: string[];
  warnings: string[];
}

// Предупреждение «Не распознано слоёв…» дублирует саму сводку.
const isUnrecognizedWarning = (w: string) => w.startsWith("Не распознано слоёв");

export function layerSummary(scene: Scene | null): LayerSummary | null {
  const layers = scene?.meta.layers;
  if (!layers?.length) return null;
  return {
    total: layers.length,
    unrecognized: layers.filter((l) => !l.label).map((l) => l.displayName ?? l.name),
    warnings: (scene?.meta.warnings ?? []).filter((w) => !isUnrecognizedWarning(w)),
  };
}

// Окно «Слои чертежа»: что разбор взял из каждого слоя и назначения слоёв
// пользователем. Применение -- разбор того же исходника заново на сервере.
export function useLayerMapping({
  scene,
  sceneLoadToken,
  loadScene,
  onReparsed,
}: {
  scene: Scene | null;
  sceneLoadToken: number;
  loadScene: (scene: Scene) => void;
  onReparsed: () => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [kinds, setKinds] = useState<LayerKind[]>([]);
  // Сводка над сценой закрыта для этой загрузки; новая загрузка покажет её снова.
  const [dismissedToken, setDismissedToken] = useState<number | null>(null);

  const loadKinds = useAsyncTask(async () => {
    if (kinds.length === 0) setKinds(await getLayerKinds());
  });

  const [boundaryOpen, setBoundaryOpen] = useState(false);

  // Назначения слоёв применяются вместе с уже выбранной границей участка.
  const reparse = useAsyncTask(async (layerMap: Record<string, string>) => {
    const sourceId = scene?.meta.sourceId;
    if (!sourceId) throw new Error("Исходного чертежа на сервере нет -- загрузите его заново");
    loadScene(await reparseScene(sourceId, layerMap, scene?.meta.boundaryOverride));
    onReparsed();
    setDialogOpen(false);
  });

  // Граница участка из кандидатов -- с текущими назначениями слоёв.
  const chooseBoundary = useAsyncTask(async (candidate: BoundaryCandidate) => {
    const sourceId = scene?.meta.sourceId;
    if (!sourceId) throw new Error("Исходного чертежа на сервере нет -- загрузите его заново");
    loadScene(await reparseScene(sourceId, scene?.meta.layerMap ?? {}, candidate.points));
    onReparsed();
    setBoundaryOpen(false);
  });

  const { run: runLoadKinds } = loadKinds;
  const open = useCallback(() => {
    setDialogOpen(true);
    void runLoadKinds();
  }, [runLoadKinds]);

  const summary = layerSummary(scene);
  const showSummary = summary !== null && dismissedToken !== sceneLoadToken && (summary.unrecognized.length > 0 || summary.warnings.length > 0);

  return {
    dialogOpen,
    setDialogOpen,
    open,
    kinds,
    loadKinds,
    reparse,
    summary: showSummary ? summary : null,
    hasLayers: Boolean(scene?.meta.layers?.length),
    dismissSummary: () => setDismissedToken(sceneLoadToken),
    boundaryCandidates: scene?.meta.sourceId ? (scene.meta.boundaryCandidates ?? []) : [],
    boundaryOpen,
    setBoundaryOpen,
    chooseBoundary,
  };
}
