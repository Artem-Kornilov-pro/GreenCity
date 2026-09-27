import { Loader2, X } from "lucide-react";

// Полоса под топбаром: ошибка важнее всего, затем ход конвертации DWG,
// затем предупреждение о несконвертированных файлах папки.
export function StatusBanners({
  error,
  dwgUploading,
  totalDwgFiles,
  dwgWarnings,
  onDismissDwgWarnings,
}: {
  error: string | null;
  dwgUploading: boolean;
  totalDwgFiles: number;
  dwgWarnings: { file: string; error: string }[] | null;
  onDismissDwgWarnings: () => void;
}) {
  if (error) {
    return (
      <div className="absolute inset-x-0 top-14 z-30 border-b border-danger-500/20 bg-danger-500/90 px-4 py-2 text-sm text-white backdrop-blur-sm">
        {error}
      </div>
    );
  }
  if (dwgUploading) {
    return (
      <div className="absolute inset-x-0 top-14 z-30 flex items-center gap-2 border-b border-brand-500/20 bg-brand-500/90 px-4 py-2 text-sm text-white backdrop-blur-sm">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span>
          Конвертация {totalDwgFiles} DWG-файлов в DXF на сервере -- на крупных реальных файлах это может занять до минуты, не
          закрывайте страницу.
        </span>
      </div>
    );
  }
  if (dwgWarnings && dwgWarnings.length > 0) {
    // Побайтно одинаковый файл пачки бэкенд пропускает сам (dwg_batch_converter.
    // _skip_identical_files) -- это не ошибка, и в общем списке "не удалось"
    // он пугал: пользователь видел отказ там, где всё отработало как надо.
    const duplicates = dwgWarnings.filter((w) => w.error.includes("пропущен как дубль"));
    const failures = dwgWarnings.filter((w) => !duplicates.includes(w));
    return (
      <div className="absolute inset-x-0 top-14 z-30 flex items-start justify-between gap-3 border-b border-warning-500/30 bg-warning-500/90 px-4 py-2 text-sm text-ink-900 backdrop-blur-sm">
        <span className="flex flex-col gap-0.5">
          {failures.length > 0 && (
            <span>
              Сцена загружена, но {failures.length} из {totalDwgFiles} .dwg-файлов не удалось сконвертировать:{" "}
              {failures.map((w) => `${w.file} (${w.error})`).join("; ")}
            </span>
          )}
          {duplicates.length > 0 && (
            <span>
              Пропущены одинаковые файлы: {duplicates.map((w) => `${w.file} — ${w.error.replace(/ -- пропущен как дубль$/, "")}`).join("; ")}
            </span>
          )}
        </span>
        <button onClick={onDismissDwgWarnings} className="shrink-0 opacity-70 hover:opacity-100">
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }
  return null;
}
