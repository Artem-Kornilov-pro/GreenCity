import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Eye, FolderOpen, Leaf, Loader2, X } from "lucide-react";
import { downloadSharedFile, loadSharedProject, type ProjectFile, type SharedProject } from "../api";
import { PageTransition } from "../components/PageTransition";
import { ProjectFilesList } from "../components/ProjectFilesList";
import { Button } from "../components/ui/button";
import { downloadBlob } from "../lib/download";
import { errorMessage } from "../lib/http";
import { SceneView } from "../scene/SceneView";
import { useCatalog } from "./editor/hooks/useCatalog";

const noop = () => undefined;

// Проект по ссылке (issue #150): без входа, только просмотр -- план на
// 3D-сцене и файлы проекта (записка, объяснения, чертёж, визуализации).
export default function SharedProjectPage() {
  const { token = "" } = useParams<{ token: string }>();
  const catalog = useCatalog();
  const [project, setProject] = useState<SharedProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filesOpen, setFilesOpen] = useState(true);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadSharedProject(token)
      .then((p) => alive && setProject(p))
      .catch((e) => alive && setError(errorMessage(e)));
    return () => {
      alive = false;
    };
  }, [token]);

  const loadBlob = useCallback((file: ProjectFile) => downloadSharedFile(token, file.id), [token]);
  const download = async (file: ProjectFile) => {
    setDownloadingId(file.id);
    try {
      downloadBlob(await loadBlob(file), file.name);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <PageTransition>
      <div className="relative flex h-screen flex-col overflow-hidden bg-ink-50">
        <header className="z-30 flex items-center gap-3 border-b border-ink-200/70 bg-white/85 px-4 py-2.5 backdrop-blur">
          <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold text-ink-900">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
              <Leaf className="h-4 w-4" />
            </span>
            GreenCity
          </Link>
          <span className="truncate text-sm text-ink-700">{project?.name ?? "Проект по ссылке"}</span>
          <span className="flex items-center gap-1 rounded-full bg-ink-100 px-2 py-0.5 text-xs text-ink-600">
            <Eye className="h-3 w-3" />
            только просмотр
          </span>
          <div className="flex-1" />
          {project && (
            <Button size="sm" variant="outline" onClick={() => setFilesOpen((v) => !v)}>
              <FolderOpen className="h-3.5 w-3.5" />
              Файлы{project.files.length ? ` · ${project.files.length}` : ""}
            </Button>
          )}
        </header>

        <main className="relative flex-1">
          {project && (
            <SceneView
              scene={project.scene}
              sceneLoadToken={1}
              catalogById={catalog.catalogById}
              availableModels={catalog.availableModels}
              selectedId={null}
              transformMode="translate"
              selectionMode={false}
              onSelect={noop}
              onMove={noop}
              onRotate={noop}
              onHoverZone={noop}
              onAreaSelected={noop}
            />
          )}
          {!project && !error && (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-ink-400" />
            </div>
          )}
          {error && (
            <div className="absolute inset-x-0 top-6 mx-auto w-fit max-w-lg rounded-xl bg-white px-5 py-4 text-center text-sm text-ink-700 shadow-soft">
              {error}
            </div>
          )}
          {project && filesOpen && (
            <aside className="scrollbar-thin absolute bottom-3 right-3 top-3 z-20 flex w-[min(30rem,calc(100%-1.5rem))] flex-col gap-3 overflow-y-auto rounded-2xl border border-ink-200/70 bg-white/95 p-4 shadow-soft-lg backdrop-blur">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-ink-900">Файлы проекта</h2>
                <Button size="sm" variant="ghost" onClick={() => setFilesOpen(false)} title="Скрыть">
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
              {project.files.length ? (
                <ProjectFilesList files={project.files} onDownload={(f) => void download(f)} loadBlob={loadBlob} downloadingId={downloadingId} />
              ) : (
                <p className="text-sm text-ink-500">Файлов пока нет.</p>
              )}
            </aside>
          )}
        </main>
      </div>
    </PageTransition>
  );
}
