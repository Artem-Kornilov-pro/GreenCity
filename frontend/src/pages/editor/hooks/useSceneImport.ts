import { useCallback, useState } from "react";
import { uploadDwgFolder, uploadDxf } from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import type { Scene } from "../../../types";

export type DwgWarning = { file: string; error: string };

const isDxf = (f: File) => f.name.toLowerCase().endsWith(".dxf");
const isDwg = (f: File) => f.name.toLowerCase().endsWith(".dwg");
const isZip = (f: File) => f.name.toLowerCase().endsWith(".zip");
const isDrawing = (f: File) => isDxf(f) || isDwg(f) || isZip(f);

// Загрузка чертежа: один .dxf -- как есть, иначе комплект объекта (.dwg,
// .dxf, .zip -- файлы, папка или архив) собирается в одну сцену на сервере.
// Кнопки, перетаскивание и «Повторить» идут через одни и те же задачи.
export function useSceneImport(loadScene: (scene: Scene) => void) {
  // .dwg, которые не удалось сконвертировать, -- предупреждение, а не
  // ошибка: сцена уже загружена. totalDwgFiles -- сколько файлов отправлено.
  const [dwgWarnings, setDwgWarnings] = useState<DwgWarning[] | null>(null);
  const [totalDwgFiles, setTotalDwgFiles] = useState(0);

  const dxf = useAsyncTask(async (file: File) => {
    setDwgWarnings(null);
    loadScene(await uploadDxf(file));
  });

  // В папке рядом с чертежами лежат PDF, xlsx, фото -- отправляем только
  // чертежи и архивы.
  const dwg = useAsyncTask(async (files: File[]) => {
    const drawingFiles = files.filter(isDrawing);
    if (drawingFiles.length === 0) throw new Error("Среди выбранных файлов нет .dwg, .dxf или .zip");
    setDwgWarnings(null);
    setTotalDwgFiles(drawingFiles.length);
    const parsed = await uploadDwgFolder(drawingFiles);
    loadScene(parsed);
    setDwgWarnings(parsed.dwgConversionWarnings ?? null);
  });

  // Выбранное или перетащенное: единственный чертёж-.dxf -- открыть его;
  // иначе -- комплектом. Какие .dxf рядом с .dwg брать, решает сервер (копию
  // DWG в DXF он пропускает и называет в предупреждениях).
  const { run: runDxf } = dxf;
  const { run: runDwg } = dwg;
  const importFiles = useCallback(
    (files: File[]) => {
      const drawings = files.filter(isDrawing);
      if (drawings.length === 1 && isDxf(drawings[0])) void runDxf(drawings[0]);
      else void runDwg(files);
    },
    [runDxf, runDwg],
  );

  return {
    uploadDwg: dwg.run,
    importFiles,
    dxf,
    dwg,
    dwgWarnings,
    totalDwgFiles,
    dismissDwgWarnings: () => setDwgWarnings(null),
  };
}
