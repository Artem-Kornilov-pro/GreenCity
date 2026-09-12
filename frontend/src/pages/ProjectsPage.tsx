import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { FolderKanban, Leaf, Pencil, Plus, Trash2, ArrowRight, LogOut } from "lucide-react";
import { deleteProject, listProjects, renameProject, type ProjectSummary } from "../api";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { PageTransition } from "../components/PageTransition";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "../components/ui/dialog";
import { useAuth } from "../context/useAuth";

const MAX_PROJECTS = 3; // держим в паре с backend/projects.py::MAX_PROJECTS_PER_USER — лимит проверяет бэкенд, тут только для подсказки

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export default function ProjectsPage() {
  const { session, initializing, logout } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<ProjectSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);

  const refresh = useCallback(() => {
    if (!session) return;
    setLoading(true);
    listProjects(session)
      .then(setProjects)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [session]);

  useEffect(() => {
    const run = () => {
      if (!session) return;
      setLoading(true);
      listProjects(session)
        .then(setProjects)
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    };
    run();
  }, [session]);

  if (!initializing && !session) {
    return <Navigate to="/login" replace state={{ from: "/projects" }} />;
  }

  async function handleRename() {
    if (!session || !renaming || !renameValue.trim()) return;
    setBusyId(renaming.id);
    try {
      await renameProject(session, renaming.id, renameValue.trim());
      setRenaming(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete() {
    if (!session || !deleting) return;
    setBusyId(deleting.id);
    try {
      await deleteProject(session, deleting.id);
      setDeleting(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <PageTransition>
      <div className="min-h-full bg-ink-50">
        <header className="border-b border-ink-200/60 bg-white">
          <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
            <Link to="/" className="flex items-center gap-2 font-semibold text-ink-900">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
                <Leaf className="h-4.5 w-4.5" />
              </span>
              GreenCity
            </Link>
            <div className="flex items-center gap-3">
              <span className="text-sm text-ink-500">{session?.username}</span>
              <Button variant="ghost" size="sm" onClick={logout}>
                <LogOut className="h-4 w-4" />
                Выйти
              </Button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-5xl px-6 py-12">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-ink-900">Мои проекты</h1>
              <p className="mt-1 text-sm text-ink-500">
                {projects.length} из {MAX_PROJECTS}
              </p>
            </div>
            <Button onClick={() => navigate("/editor")} disabled={projects.length >= MAX_PROJECTS} title={projects.length >= MAX_PROJECTS ? `Лимит ${MAX_PROJECTS} проектов — удалите один, чтобы создать новый` : undefined}>
              <Plus className="h-4 w-4" />
              Новый проект
            </Button>
          </div>

          {error && <p className="mt-4 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">{error}</p>}

          {loading ? (
            <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-40 animate-pulse rounded-2xl bg-ink-100" />
              ))}
            </div>
          ) : projects.length === 0 ? (
            <div className="mt-10 flex flex-col items-center gap-3 rounded-2xl border border-dashed border-ink-300 bg-white py-16 text-center">
              <FolderKanban className="h-10 w-10 text-ink-300" />
              <p className="text-ink-500">Проектов пока нет — загрузите DXF в редакторе и сохраните первый.</p>
              <Button className="mt-2" onClick={() => navigate("/editor")}>
                <Plus className="h-4 w-4" />
                Создать проект
              </Button>
            </div>
          ) : (
            <motion.div layout className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <AnimatePresence>
                {projects.map((p) => (
                  <motion.div
                    key={p.id}
                    layout
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.96 }}
                    transition={{ duration: 0.18 }}
                  >
                    <Card className="group flex h-full flex-col justify-between p-5 transition-shadow hover:shadow-soft-lg">
                      <div>
                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-100 text-brand-700">
                          <FolderKanban className="h-4.5 w-4.5" />
                        </div>
                        <h3 className="mt-3 truncate font-semibold text-ink-900" title={p.name}>
                          {p.name}
                        </h3>
                        <p className="mt-1 text-xs text-ink-400">Изменено {formatDate(p.updated_at)}</p>
                      </div>
                      <div className="mt-4 flex items-center gap-1.5">
                        <Button size="sm" className="flex-1" onClick={() => navigate(`/editor/${p.id}`)}>
                          Открыть
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          size="icon"
                          variant="outline"
                          title="Переименовать"
                          disabled={busyId === p.id}
                          onClick={() => {
                            setRenaming(p);
                            setRenameValue(p.name);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button size="icon" variant="danger" title="Удалить" disabled={busyId === p.id} onClick={() => setDeleting(p)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </Card>
                  </motion.div>
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </main>
      </div>

      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Переименовать проект</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleRename();
            }}
            className="flex flex-col gap-3"
          >
            <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} autoFocus />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
                Отмена
              </Button>
              <Button type="submit" disabled={!renameValue.trim() || busyId === renaming?.id}>
                Сохранить
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить «{deleting?.name}»?</DialogTitle>
            <DialogDescription>Это действие необратимо — план и все объекты в нём будут потеряны.</DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Отмена
            </Button>
            <Button variant="danger" disabled={busyId === deleting?.id} onClick={handleDelete}>
              Удалить
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </PageTransition>
  );
}
