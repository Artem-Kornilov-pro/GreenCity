import { useEffect, useState } from "react";
import { Download, FileSpreadsheet, FileText, Image as ImageIcon, Loader2, PencilRuler, ShieldCheck, Trash2 } from "lucide-react";
import type { ProjectFile } from "../api";
import { Button } from "./ui/button";

// Список файлов проекта (issue #150) -- в редакторе и на странице проекта по
// ссылке. Визуализации -- превью; скачивание -- в браузер, без ухода со
// страницы (lib/download.ts).

const GROUPS: { title: string; kinds: string[] }[] = [
  { title: "Документы", kinds: ["docx", "explanations_xlsx", "explanations_csv", "explanations_json"] },
  { title: "Чертёж", kinds: ["dxf", "export_check"] },
  { title: "Визуализации", kinds: ["visualization"] },
];

function iconOf(kind: string) {
  if (kind === "docx") return FileText;
  if (kind.startsWith("explanations")) return FileSpreadsheet;
  if (kind === "dxf") return PencilRuler;
  if (kind === "export_check") return ShieldCheck;
  return ImageIcon;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}

// Подпись под картинкой -- ракурс из имени файла: «Визуализация — Двор — общий вид.jpg» -> «общий вид».
function caption(file: ProjectFile): string {
  const base = file.name.replace(/\.[^.]+$/, "");
  const parts = base.split(" — ");
  return parts.length > 1 ? parts[parts.length - 1] : base;
}

function Preview({ file, loadBlob }: { file: ProjectFile; loadBlob: (file: ProjectFile) => Promise<Blob> }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let objectUrl: string | null = null;
    loadBlob(file)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file, loadBlob]);
  return url ? (
    <img src={url} alt={file.name} className="aspect-video w-full rounded-lg border border-ink-100 object-cover" />
  ) : (
    <div className="flex aspect-video w-full items-center justify-center rounded-lg border border-ink-100 bg-ink-50">
      <Loader2 className="h-4 w-4 animate-spin text-ink-400" />
    </div>
  );
}

export function ProjectFilesList({
  files,
  onDownload,
  onDelete,
  loadBlob,
  downloadingId,
}: {
  files: ProjectFile[];
  onDownload: (file: ProjectFile) => void;
  // Нет -- просмотр по ссылке, удалять нельзя.
  onDelete?: (file: ProjectFile) => void;
  loadBlob: (file: ProjectFile) => Promise<Blob>;
  downloadingId?: string | null;
}) {
  const known = new Set(GROUPS.flatMap((g) => g.kinds));
  const groups = [...GROUPS, { title: "Другие", kinds: [...new Set(files.map((f) => f.kind).filter((k) => !known.has(k)))] }];
  return (
    <div className="flex flex-col gap-4">
      {groups.map(({ title, kinds }) => {
        const items = files.filter((f) => kinds.includes(f.kind)).sort((a, b) => kinds.indexOf(a.kind) - kinds.indexOf(b.kind));
        if (!items.length) return null;
        const pictures = title === "Визуализации";
        return (
          <section key={title} className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">{title}</h3>
            <ul className={pictures ? "grid grid-cols-1 gap-3 sm:grid-cols-3" : "flex flex-col gap-1.5"}>
              {items.map((file) => {
                const Icon = iconOf(file.kind);
                return (
                  <li key={file.id} className={pictures ? "flex flex-col gap-1.5" : "flex items-center gap-2 rounded-lg border border-ink-100 px-2.5 py-1.5"}>
                    {pictures && <Preview file={file} loadBlob={loadBlob} />}
                    <div className="flex min-w-0 flex-1 items-center gap-2">
                      {!pictures && <Icon className="h-4 w-4 shrink-0 text-ink-500" />}
                      <span className="min-w-0 flex-1 truncate text-sm text-ink-800" title={file.name}>
                        {pictures ? caption(file) : file.name}
                      </span>
                      <span className="shrink-0 text-xs text-ink-400">{formatSize(file.size)}</span>
                      <Button size="sm" variant="outline" onClick={() => onDownload(file)} disabled={downloadingId === file.id} title="Скачать">
                        {downloadingId === file.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                      </Button>
                      {onDelete && (
                        <Button size="sm" variant="ghost" onClick={() => onDelete(file)} title="Удалить файл">
                          <Trash2 className="h-3.5 w-3.5 text-ink-400" />
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
