import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";
import { UploadCloud } from "lucide-react";
import { SceneView } from "../../scene/SceneView";
import { objectDisplayName } from "../../catalog";
import type { RestrictionZone } from "../../types";
import { PageTransition } from "../../components/PageTransition";
import type { AsyncTask } from "../../hooks/useAsyncTask";
import { selectSession } from "../../store/authSlice";
import { useAppSelector } from "../../store/hooks";
import { AssistantPanel } from "./AssistantPanel";
import { EditorSidebar } from "./EditorSidebar";
import { EditorTopBar } from "./EditorTopBar";
import { GreenPlanOptionsDialog } from "./GreenPlanOptionsDialog";
import { GreenPlanPanel } from "./GreenPlanPanel";
import { SaveAsDialog } from "./SaveAsDialog";
import { StatusBanners, type BannerError } from "./StatusBanners";
import { useAssistant } from "./hooks/useAssistant";
import { useCatalog } from "./hooks/useCatalog";
import { useExport } from "./hooks/useExport";
import { useFileDrop } from "./hooks/useFileDrop";
import { useGreenPlan } from "./hooks/useGreenPlan";
import { useProject } from "./hooks/useProject";
import { useSceneEditing } from "./hooks/useSceneEditing";
import { useSceneImport } from "./hooks/useSceneImport";

type RightPanel = "assistant" | "greenplan" | null;

type TaskStatus = Pick<AsyncTask<[], unknown>, "error" | "retry" | "dismiss">;

// Первая упавшая задача -- в баннер, с кнопкой «Повторить».
function firstError(tasks: TaskStatus[]): BannerError | null {
  const failed = tasks.find((t) => t.error);
  return failed?.error ? { message: failed.error, onRetry: failed.retry, onDismiss: failed.dismiss } : null;
}

// Страница редактора: состояние и действия -- в хуках (./hooks), отрисовка --
// по частям в этой же папке (топбар, левая панель, панели Ассистента и
// GreenPlan, диалоги).
export default function EditorPage() {
  const { projectId } = useParams<{ projectId?: string }>();
  const session = useAppSelector(selectSession);
  // Правые панели занимают одно место -- открытая одна закрывает другую.
  const [rightPanel, setRightPanel] = useState<RightPanel>(null);
  const [hoveredZone, setHoveredZone] = useState<RestrictionZone | null>(null);

  const editor = useSceneEditing();
  const { scene } = editor;
  const catalog = useCatalog();
  const importer = useSceneImport(editor.loadScene);
  const project = useProject({ projectId, session, scene, loadScene: editor.loadScene });
  const exporter = useExport(scene);
  const greenPlan = useGreenPlan({
    scene,
    replaceScene: editor.replaceScene,
    projectName: project.name,
    onResult: () => setRightPanel("greenplan"),
  });
  // Отменили правку ассистента, запускавшую GreenPlan, -- его результат тоже.
  const { clearResult } = greenPlan;
  const onGreenPlanUndone = useCallback(() => {
    clearResult();
    setRightPanel((p) => (p === "greenplan" ? null : p));
  }, [clearResult]);
  const assistant = useAssistant({
    scene,
    replaceScene: editor.replaceScene,
    runGreenPlan: greenPlan.generate.run,
    onGreenPlanUndone,
  });

  const uploading = importer.dxf.busy || importer.dwg.busy || project.load.busy;
  const { dragging, dropHandlers } = useFileDrop(importer.importFiles, !uploading);
  const togglePanel = (panel: Exclude<RightPanel, null>) => setRightPanel((p) => (p === panel ? null : panel));

  const error = firstError([
    importer.dxf,
    importer.dwg,
    project.load,
    project.save,
    project.saveAs,
    greenPlan.generate,
    exporter.dxf,
    greenPlan.docx,
    greenPlan.explanations,
    catalog.load,
  ]);

  return (
    <PageTransition>
      <div className="relative flex h-screen flex-col overflow-hidden bg-ink-50" {...dropHandlers}>
        <EditorTopBar
          title={project.name ?? (projectId ? "Загрузка…" : "Новый проект")}
          dxfLoading={importer.dxf.busy}
          dwgLoading={importer.dwg.busy}
          projectLoading={project.load.busy}
          onDxfFile={importer.uploadDxf}
          onDwgFolder={importer.uploadDwg}
          sceneLoaded={scene !== null}
          selectionMode={editor.selectionMode}
          onToggleSelection={() => editor.setSelectionMode((v) => !v)}
          assistantOpen={rightPanel === "assistant"}
          onToggleAssistant={() => togglePanel("assistant")}
          greenPlanBusy={greenPlan.generate.busy}
          onGreenPlan={() => greenPlan.setDialogOpen(true)}
          exportingDxf={exporter.dxf.busy}
          onExportDxf={() => void exporter.dxf.run()}
          onExportJson={exporter.exportJson}
          isSavedProject={Boolean(projectId)}
          projectBusy={project.busy}
          onSave={project.saveOrAsk}
        />

        <StatusBanners
          notice={exporter.notice}
          onDismissNotice={exporter.dismissNotice}
          error={error}
          dwgUploading={importer.dwg.busy}
          totalDwgFiles={importer.totalDwgFiles}
          dwgWarnings={importer.dwgWarnings}
          onDismissDwgWarnings={importer.dismissDwgWarnings}
        />

        <main className="relative flex-1 overflow-hidden">
          {scene ? (
            <SceneView
              scene={scene}
              sceneLoadToken={editor.sceneLoadToken}
              catalogById={catalog.catalogById}
              availableModels={catalog.availableModels}
              selectedId={editor.selectedId}
              transformMode={editor.transformMode}
              selectionMode={editor.selectionMode}
              onSelect={editor.select}
              onMove={editor.move}
              onRotate={editor.rotate}
              onHoverZone={setHoveredZone}
              onAreaSelected={editor.selectArea}
            />
          ) : (
            // Место под сцену, пока её нет: сюда же бросают файл.
            <div className="flex h-full items-center justify-center pl-80">
              <div className="flex flex-col items-center gap-3 rounded-3xl border-2 border-dashed border-ink-300 px-16 py-14 text-center text-ink-400">
                <UploadCloud className="h-12 w-12" />
                <p className="text-base font-medium text-ink-600">Перетащите сюда DXF-файл или папку с DWG</p>
                <p className="text-sm">или выберите их кнопками слева</p>
              </div>
            </div>
          )}

          <EditorSidebar
            sceneLoaded={scene !== null}
            onDxfFile={importer.uploadDxf}
            onDwgFolder={importer.uploadDwg}
            dxfLoading={importer.dxf.busy}
            dwgLoading={importer.dwg.busy}
            catalog={catalog.catalog}
            filteredCatalog={catalog.filteredCatalog}
            availableModelsCount={catalog.availableModels.size}
            catalogFilter={catalog.filter}
            onCatalogFilterChange={catalog.setFilter}
            selectedItemId={catalog.selectedItemId}
            onSelectItem={catalog.setSelectedItemId}
            selectedItem={catalog.selectedItem}
            onAddObject={editor.addObject}
            selectedArea={editor.selectedArea}
            onClearSelection={editor.clearArea}
            hoveredZone={hoveredZone}
            selectedObject={editor.selectedObject}
            selectedObjectName={editor.selectedObject ? objectDisplayName(editor.selectedObject, catalog.catalogById) : null}
            selectedViolations={editor.selectedViolations}
            transformMode={editor.transformMode}
            onTransformModeChange={editor.setTransformMode}
            onDelete={editor.remove}
          />

          <AssistantPanel
            open={rightPanel === "assistant"}
            onToggle={() => togglePanel("assistant")}
            onClose={() => setRightPanel(null)}
            messages={assistant.messages}
            editing={assistant.editing}
            instruction={assistant.instruction}
            onInstructionChange={assistant.setInstruction}
            onSubmit={assistant.submit}
            sceneLoaded={scene !== null}
            undoableMessageId={assistant.undoableMessageId}
            onUndo={assistant.undo}
            onRetry={assistant.retry}
          />

          <GreenPlanPanel
            open={rightPanel === "greenplan"}
            onToggle={() => togglePanel("greenplan")}
            onClose={() => setRightPanel(null)}
            result={greenPlan.result}
            reportLoading={greenPlan.reportLoading}
            documentBusy={greenPlan.docx.busy || greenPlan.explanations.busy}
            onDownloadDocument={() => void greenPlan.docx.run()}
            onDownloadExplanations={(format) => void greenPlan.explanations.run(format)}
            onRetryReport={greenPlan.retryReport}
          />
        </main>

        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-brand-600/15 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-2 rounded-3xl border-2 border-dashed border-brand-500 bg-white/90 px-14 py-10 text-brand-700 shadow-soft-lg">
              <UploadCloud className="h-10 w-10" />
              <p className="text-base font-semibold">Отпустите, чтобы загрузить</p>
              <p className="text-sm text-ink-500">.dxf — откроется он, .dwg или папка — сконвертируется на сервере</p>
            </div>
          </div>
        )}
      </div>

      <GreenPlanOptionsDialog
        open={greenPlan.dialogOpen}
        onOpenChange={greenPlan.setDialogOpen}
        catalog={catalog.catalog}
        initial={greenPlan.options}
        busy={greenPlan.generate.busy}
        onRun={(options) => void greenPlan.generate.run(options)}
      />
      <SaveAsDialog
        open={project.saveAsOpen}
        onOpenChange={project.setSaveAsOpen}
        name={project.saveAsName}
        onNameChange={project.setSaveAsName}
        busy={project.busy}
        onSave={() => void project.saveAs.run()}
      />
    </PageTransition>
  );
}
