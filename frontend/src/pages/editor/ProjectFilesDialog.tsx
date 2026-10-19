import { useState } from "react";
import { Check, Copy, Link2, Loader2 } from "lucide-react";
import { shareUrl, type ProjectFile } from "../../api";
import { ProjectFilesList } from "../../components/ProjectFilesList";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";

// Файлы сохранённого проекта: что готово после GreenPlan, что готовится,
// что не удалось.
export function ProjectFilesDialog({
  open,
  onOpenChange,
  files,
  preparing,
  notes,
  prepareError,
  onRetry,
  onDownload,
  onDelete,
  loadBlob,
  downloadingId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  files: ProjectFile[];
  preparing: boolean;
  notes: string[];
  prepareError: string | null;
  onRetry: () => void;
  onDownload: (file: ProjectFile) => void;
  onDelete: (file: ProjectFile) => void;
  loadBlob: (file: ProjectFile) => Promise<Blob>;
  downloadingId: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="scrollbar-thin max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Файлы проекта</DialogTitle>
          <p className="text-sm text-ink-500">
            Готовятся сами после GreenPlan: пояснительная записка, объяснения посадок, чертёж DXF с проверкой и визуализации. Следующий запуск
            GreenPlan заменяет комплект.
          </p>
        </DialogHeader>
        {preparing && (
          <p className="flex items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-800">
            <Loader2 className="h-4 w-4 animate-spin" />
            Готовим файлы по результату GreenPlan — на крупном объекте это несколько минут, можно продолжать работу.
          </p>
        )}
        {prepareError && (
          <p className="flex items-center justify-between gap-2 rounded-lg bg-danger-500/10 px-3 py-2 text-sm text-danger-500">
            {prepareError}
            <Button size="sm" variant="outline" onClick={onRetry}>
              Повторить
            </Button>
          </p>
        )}
        {notes.length > 0 && (
          <ul className="flex list-disc flex-col gap-1 rounded-lg bg-warning-500/10 py-2 pl-7 pr-3 text-sm text-ink-700">
            {notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
        {files.length ? (
          <ProjectFilesList files={files} onDownload={onDownload} onDelete={onDelete} loadBlob={loadBlob} downloadingId={downloadingId} />
        ) : (
          !preparing && <p className="text-sm text-ink-500">Файлов пока нет — запустите GreenPlan.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

// Доступ по ссылке: включить, скопировать ссылку, выключить.
export function ShareDialog({
  open,
  onOpenChange,
  token,
  busy,
  error,
  onToggle,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  token: string | null;
  busy: boolean;
  error: string | null;
  onToggle: (enabled: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);
  const url = token ? shareUrl(token) : null;
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Поделиться проектом</DialogTitle>
          <p className="text-sm text-ink-500">
            По ссылке проект открывается без входа и только для просмотра: план на 3D-сцене и файлы проекта. Сохранённые позже изменения видны
            по той же ссылке.
          </p>
        </DialogHeader>
        {url ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <input readOnly value={url} onFocus={(e) => e.currentTarget.select()} className="min-w-0 flex-1 rounded-lg border border-ink-200 bg-ink-50 px-3 py-2 text-sm" />
              <Button size="sm" onClick={copy}>
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Скопировано" : "Копировать"}
              </Button>
            </div>
            <Button size="sm" variant="outline" onClick={() => onToggle(false)} disabled={busy} className="self-start">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Закрыть доступ по ссылке
            </Button>
          </div>
        ) : (
          <Button onClick={() => onToggle(true)} disabled={busy} className="self-start">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
            Открыть доступ по ссылке
          </Button>
        )}
        {error && <p className="text-sm text-danger-500">{error}</p>}
      </DialogContent>
    </Dialog>
  );
}
