import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteProjectFile,
  downloadProjectFile,
  prepareGreenPlanFiles,
  setProjectSharing,
  type ProjectFile,
} from "../../../api";
import type { Session } from "../../../auth";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import { downloadBlob } from "../../../lib/download";
import type { Scene } from "../../../types";
import type { GreenPlanState } from "../editorTypes";

// Файлы сохранённого проекта (issue #150): после GreenPlan комплект --
// записка, объяснения, DXF с проверкой, визуализации -- готовится на сервере
// сам, когда готов текст-обоснование (он идёт в записку). У несохранённого
// проекта -- подсказка сохранить: после «Сохранить как…» комплект готовится
// для того же результата.
export function useProjectFiles({
  projectId,
  session,
  scene,
  greenPlan,
  reportLoading,
  files,
  setFiles,
  shareToken,
  setShareToken,
}: {
  projectId: string | undefined;
  session: Session | null;
  scene: Scene | null;
  greenPlan: GreenPlanState | null;
  reportLoading: boolean;
  files: ProjectFile[];
  setFiles: (files: ProjectFile[]) => void;
  shareToken: string | null;
  setShareToken: (token: string | null) => void;
}) {
  const [notes, setNotes] = useState<string[]>([]);
  // Для какого результата GreenPlan комплект уже заказан: один запуск --
  // один комплект (визуализации платные).
  const preparedFor = useRef<GreenPlanState | null>(null);

  const prepare = useAsyncTask(async (result: GreenPlanState, sceneNow: Scene) => {
    if (!session || !projectId) return;
    const done = await prepareGreenPlanFiles(session, projectId, {
      scene: sceneNow,
      assignments: result.assignments,
      rejections: result.rejections ?? { detailed: [], total: 0, by_rule: {} },
      report: result.report,
      options: result.options,
      notes: result.notes,
      visualizations: true,
    });
    setFiles(done.files);
    setNotes(done.notes);
  });
  const { run: runPrepare } = prepare;

  useEffect(() => {
    if (!greenPlan || reportLoading || !session || !projectId || !scene) return;
    if (preparedFor.current === greenPlan) return;
    preparedFor.current = greenPlan;
    void runPrepare(greenPlan, scene);
  }, [greenPlan, reportLoading, session, projectId, scene, runPrepare]);

  const download = useAsyncTask(async (file: ProjectFile) => {
    if (!session || !projectId) return;
    downloadBlob(await downloadProjectFile(session, projectId, file.id), file.name);
  });

  const remove = useAsyncTask(async (file: ProjectFile) => {
    if (!session || !projectId) return;
    await deleteProjectFile(session, projectId, file.id);
    setFiles(files.filter((f) => f.id !== file.id));
  });

  const share = useAsyncTask(async (enabled: boolean) => {
    if (!session || !projectId) return;
    setShareToken(await setProjectSharing(session, projectId, enabled));
  });

  const loadBlob = useCallback(
    (file: ProjectFile) => (session && projectId ? downloadProjectFile(session, projectId, file.id) : Promise.reject(new Error("нет проекта"))),
    [session, projectId],
  );

  return { files, notes, prepare, download, remove, share, shareToken, loadBlob };
}
