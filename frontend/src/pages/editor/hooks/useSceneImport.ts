import { useCallback, useState } from "react";
import { uploadDwgFolder, uploadDxf } from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import type { Scene } from "../../../types";

export type DwgWarning = { file: string; error: string };

const isDxf = (f: File) => f.name.toLowerCase().endsWith(".dxf");
const isDwg = (f: File) => f.name.toLowerCase().endsWith(".dwg");

// Загрузка чертежа: один .dxf или набор .dwg (папка проекта). Кнопки,
// перетаскивание и «Повторить» идут через одни и те же задачи.
export function useSceneImport(loadScene: (scene: Scene) => void) {
  // .dwg, которые не удалось сконвертировать, -- предупреждение, а не
  // ошибка: сцена уже загружена. totalDwgFiles -- сколько файлов отправлено.
  const [dwgWarnings, setDwgWarnings] = useState<DwgWarning[] | null>(null);
  const [totalDwgFiles, setTotalDwgFiles] = useState(0);

  const dxf = useAsyncTask(async (file: File) => {
    setDwgWarnings(null);
    loadScene(await uploadDxf(file));
  });

  // В папке рядом с .dwg лежат PDF, xlsx, фото -- отправляем только .dwg.
  const dwg = useAsyncTask(async (files: File[]) => {
    const dwgFiles = files.filter(isDwg);
    if (dwgFiles.length === 0) throw new Error("Среди выбранных файлов нет .dwg");
    setDwgWarnings(null);
    setTotalDwgFiles(dwgFiles.length);
    const parsed = await uploadDwgFolder(dwgFiles);
    loadScene(parsed);
    setDwgWarnings(parsed.dwgConversionWarnings ?? null);
  });

  // Перетащенные файлы: .dxf -- открыть его, иначе -- все .dwg пачкой.
  const { run: runDxf } = dxf;
  const { run: runDwg } = dwg;
  const importFiles = useCallback(
    (files: File[]) => {
      const dxfFile = files.find(isDxf);
      if (dxfFile) void runDxf(dxfFile);
      else void runDwg(files);
    },
    [runDxf, runDwg],
  );

  return {
    uploadDxf: dxf.run,
    uploadDwg: dwg.run,
    importFiles,
    dxf,
    dwg,
    dwgWarnings,
    totalDwgFiles,
    dismissDwgWarnings: () => setDwgWarnings(null),
  };
}
