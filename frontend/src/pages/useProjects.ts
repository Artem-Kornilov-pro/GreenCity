import { useEffect, useState } from "react";
import { deleteProject, listProjects, renameProject, type ProjectSummary } from "../api";
import type { Session } from "../auth";
import { useAsyncTask } from "../hooks/useAsyncTask";

// Проекты пользователя: список, переименование, удаление. После изменения
// список перечитывается с сервера.
export function useProjects(session: Session | null) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  // Список ещё ни разу не приходил -- показывать заглушки, а не «проектов нет».
  const [loaded, setLoaded] = useState(false);

  const list = useAsyncTask(async (owner: Session) => {
    try {
      setProjects(await listProjects(owner));
    } finally {
      setLoaded(true);
    }
  });
  const { run: refresh } = list;

  useEffect(() => {
    if (session) void refresh(session);
  }, [session, refresh]);

  // true -- получилось (диалог можно закрыть).
  const rename = useAsyncTask(async (id: string, name: string) => {
    if (!session) return false;
    await renameProject(session, id, name);
    void refresh(session);
    return true;
  });

  const remove = useAsyncTask(async (id: string) => {
    if (!session) return false;
    await deleteProject(session, id);
    void refresh(session);
    return true;
  });

  const failed = [list, rename, remove].find((t) => t.error);

  return {
    projects,
    loading: !loaded || (list.busy && projects.length === 0),
    rename,
    remove,
    mutating: rename.busy || remove.busy,
    error: failed ? { message: failed.error as string, retry: failed.retry, dismiss: failed.dismiss } : null,
  };
}
