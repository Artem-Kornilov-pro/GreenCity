import { useState } from "react";
import { exportDxf, verifyExport, type ExportVerification } from "../../../api";
import { useAsyncTask } from "../../../hooks/useAsyncTask";
import { downloadBlob } from "../../../lib/download";
import type { Scene } from "../../../types";

const REBUILT_NOTICE =
  "Исходный чертёж на сервере не найден — DXF собран из сцены (метры от центра участка). Чтобы получить результат поверх исходника, загрузите чертёж заново.";

// Итог независимой проверки выгрузки -- одной строкой для полосы над сценой.
function verificationNotice(report: ExportVerification): string {
  const parts = [report.source_check];
  parts.push(`посадок проверено: ${report.plants_checked}`);
  if (report.plants_missing.length) parts.push(`нет в файле: ${report.plants_missing.length}`);
  if (report.plants_moved.length) parts.push(`не на своём месте: ${report.plants_moved.length}`);
  parts.push(report.violations.length ? `нарушений норм: ${report.violations.length}` : "нарушений норм нет");
  const hash = report.source_sha256 ? ` SHA-256 исходника ${report.source_sha256.slice(0, 12)}…` : "";
  return `${report.ok ? "Проверка выгрузки пройдена" : "Проверка выгрузки нашла проблемы"}: ${parts.join("; ")}.${hash}`;
}

// Выгрузка плана: DXF (поверх исходного чертежа, если он есть на сервере) и
// объекты сцены в JSON. После DXF -- независимая проверка выгруженного файла.
export function useExport(scene: Scene | null) {
  const [notice, setNotice] = useState<string | null>(null);

  const dxf = useAsyncTask(async () => {
    if (!scene) return;
    const { blob, mode } = await exportDxf(scene);
    downloadBlob(blob, "greencity_plan.dxf");
    const rebuilt = mode === "rebuilt" ? `${REBUILT_NOTICE} ` : "";
    setNotice(`${rebuilt}Проверяем выгрузку…`);
    try {
      setNotice(rebuilt + verificationNotice(await verifyExport(scene)));
    } catch {
      setNotice(`${rebuilt}Выгрузка готова, но проверить её не удалось — повторите позже.`);
    }
  });

  const exportJson = () => {
    if (!scene) return;
    downloadBlob(new Blob([JSON.stringify(scene.objects, null, 2)], { type: "application/json" }), "objects.json");
  };

  return { dxf, exportJson, notice, dismissNotice: () => setNotice(null) };
}
