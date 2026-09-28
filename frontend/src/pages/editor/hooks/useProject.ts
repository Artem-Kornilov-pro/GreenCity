import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProject, loadProject, saveProject } from "../../../api";
import type { Session } from "../../../auth";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import type { Scene } from "../../../types";

// Сохранённый проект: загрузка по id из адреса, «Сохранить» и «Сохранить как…».
export function useProject({
  projectId,
  session,
  scene,
  loadScene,
}: {
  projectId: string | undefined;
  session: Session | null;
  scene: Scene | null;
  loadScene: (scene: Scene) => void;
}) {
  const navigate = useNavigate();
  const [name, setName] = useState<string | null>(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");

  const load = useAsyncTask(async (id: string, owner: Session) => {
    const project = await loadProject(owner, id);
    loadScene(project.scene);
    setName(project.name);
  });
  const { run: runLoad } = load;

  useEffect(() => {
    if (projectId && session) void runLoad(projectId, session);
  }, [projectId, session, runLoad]);

  const save = useAsyncTask(async () => {
    if (!session || !scene || !projectId) return;
    await saveProject(session, projectId, scene);
  });

  // Новый проект -- переход на его адрес; дальше «Сохранить» пишет в него.
  const saveAs = useAsyncTask(async () => {
    const projectName = saveAsName.trim();
    if (!session || !scene || !projectName) return;
    const created = await createProject(session, projectName, scene);
    setSaveAsOpen(false);
    setSaveAsName("");
    navigate(`/editor/${created.id}`, { replace: true });
  });

  return {
    name,
    load,
    save,
    saveAs,
    busy: save.busy || saveAs.busy,
    // Сохранённый проект -- сразу на сервер, новый -- спросить название.
    saveOrAsk: () => (projectId ? void save.run() : setSaveAsOpen(true)),
    saveAsOpen,
    setSaveAsOpen,
    saveAsName,
    setSaveAsName,
  };
}
