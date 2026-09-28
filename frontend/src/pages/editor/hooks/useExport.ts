import { useState } from "react";
import { exportDxf } from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import { downloadBlob } from "../../../lib/download";
import type { Scene } from "../../../types";

const REBUILT_NOTICE =
  "Исходный чертёж на сервере не найден — DXF собран из сцены (метры от центра участка). Чтобы получить результат поверх исходника, загрузите чертёж заново.";

// Выгрузка плана: DXF (поверх исходного чертежа, если он есть на сервере) и
// объекты сцены в JSON.
export function useExport(scene: Scene | null) {
  const [notice, setNotice] = useState<string | null>(null);

  const dxf = useAsyncTask(async () => {
    if (!scene) return;
    const { blob, mode } = await exportDxf(scene);
    downloadBlob(blob, "greencity_plan.dxf");
    setNotice(mode === "rebuilt" ? REBUILT_NOTICE : null);
  });

  const exportJson = () => {
    if (!scene) return;
    downloadBlob(new Blob([JSON.stringify(scene.objects, null, 2)], { type: "application/json" }), "objects.json");
  };

  return { dxf, exportJson, notice, dismissNotice: () => setNotice(null) };
}
