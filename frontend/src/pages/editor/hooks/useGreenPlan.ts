import { useCallback, useRef, useState } from "react";
import {
  downloadGreenPlanDocument,
  downloadGreenPlanExplanations,
  fetchGreenPlanReport,
  generateGreenPlan,
  type GreenPlanOptions,
  type GreenPlanZoneAssignment,
} from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import { downloadBlob } from "../../../lib/download";
import { errorMessage } from "../../../lib/http";
import { useAppDispatch, useAppSelector } from "../../../store/hooks";
import { selectGreenPlanOptions, setGreenPlanOptions } from "../../../store/greenPlanSlice";
import type { Scene } from "../../../types";
import type { GreenPlanState } from "../editorTypes";

// GreenPlan: параметры (в сторе), запуск, текст-обоснование (отдельный
// запрос к LLM, в фоне) и выгрузки по результату.
export function useGreenPlan({
  scene,
  replaceScene,
  projectName,
  onResult,
}: {
  scene: Scene | null;
  replaceScene: (scene: Scene) => void;
  projectName: string | null;
  // Результат готов -- показать панель GreenPlan.
  onResult: () => void;
}) {
  const dispatch = useAppDispatch();
  const options = useAppSelector(selectGreenPlanOptions);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [result, setResult] = useState<GreenPlanState | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  // Номер запроса отчёта: ответ на прошлый запуск не должен лечь поверх нового.
  const reportRequest = useRef(0);

  const loadReport = useCallback(async (assignments: GreenPlanZoneAssignment[]) => {
    const id = ++reportRequest.current;
    setReportLoading(true);
    let update: Pick<GreenPlanState, "report" | "report_error">;
    try {
      update = await fetchGreenPlanReport(assignments);
    } catch (e) {
      update = { report: null, report_error: errorMessage(e) };
    }
    if (id !== reportRequest.current) return;
    setResult((prev) => (prev ? { ...prev, ...update } : prev));
    setReportLoading(false);
  }, []);

  // baseScene -- сцена, на которой запускать, если это не текущая: ассистент
  // запускает GreenPlan на сцене своего ответа, которая в состояние ещё не
  // попала. Возвращает сцену с результатом.
  const generate = useAsyncTask(async (runOptions: GreenPlanOptions, baseScene?: Scene): Promise<Scene | null> => {
    const base = baseScene ?? scene;
    if (!base) return null;
    dispatch(setGreenPlanOptions(runOptions));
    setDialogOpen(false);
    // Повторный запуск заменяет прошлый результат GreenPlan в сцене
    // (backend/greenplan/pipeline.py), а не сажает второй слой.
    const generated = await generateGreenPlan(base, runOptions);
    replaceScene(generated.scene);
    // Расстановка, нарушения и ведомость готовы -- показываем сразу, текст
    // обоснования догружается отдельно и не задерживает результат.
    setResult({ ...generated, options: runOptions, report: null, report_error: null });
    onResult();
    void loadReport(generated.assignments);
    return generated.scene;
  });

  const clearResult = useCallback(() => setResult(null), []);

  const retryReport = useCallback(() => {
    if (result) void loadReport(result.assignments);
  }, [result, loadReport]);

  const title = projectName ?? "участок";

  const docx = useAsyncTask(async () => {
    if (!scene || !result) return;
    const blob = await downloadGreenPlanDocument(scene, result.assignments, result.report, projectName, result.options, result.notes);
    downloadBlob(blob, `Пояснительная записка — ${title}.docx`);
  });

  const explanations = useAsyncTask(async (format: "json" | "csv") => {
    if (!scene || !result) return;
    const blob = await downloadGreenPlanExplanations(scene, result.assignments, result.rejections, format);
    downloadBlob(blob, `Объяснения посадок — ${title}.${format}`);
  });

  return {
    options,
    dialogOpen,
    setDialogOpen,
    generate,
    result,
    clearResult,
    reportLoading,
    retryReport,
    docx,
    explanations,
  };
}
