import { Loader2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import type { BoundaryCandidate } from "../../types";

// Площадь участка: до гектара -- в м², дальше -- в га.
function formatArea(area: number): string {
  return area < 10_000 ? `${Math.round(area).toLocaleString("ru-RU")} м²` : `${(area / 10_000).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} га`;
}

// Контур кандидата -- маленьким SVG, вписанным в квадрат (север вверх).
function Outline({ points }: { points: number[][] }) {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const minX = Math.min(...xs);
  const maxY = Math.max(...ys);
  const size = Math.max(Math.max(...xs) - minX, maxY - Math.min(...ys)) || 1;
  const path = points.map((p) => `${(((p[0] - minX) / size) * 56 + 4).toFixed(1)},${(((maxY - p[1]) / size) * 56 + 4).toFixed(1)}`).join(" ");
  return (
    <svg viewBox="0 0 64 64" className="h-16 w-16 shrink-0 rounded-lg bg-ink-50">
      <polygon points={path} className="fill-brand-500/20 stroke-brand-600" strokeWidth={1.5} />
    </svg>
  );
}

// Слоя границы нет: граница оценена по содержимому. Пользователь выбирает
// настоящую из замкнутых контуров чертежа -- и чертёж разбирается заново с ней
// (backend: meta.boundaryCandidates, /api/reparse с boundary).
export function BoundaryChoiceDialog({
  open,
  onOpenChange,
  candidates,
  busy,
  onChoose,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidates: BoundaryCandidate[];
  busy: boolean;
  onChoose: (candidate: BoundaryCandidate) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Граница участка</DialogTitle>
          <p className="text-sm text-ink-500">
            Слоя границы в чертеже нет — сейчас граница оценена по содержимому. Выберите замкнутый контур, который и есть
            участок: чертёж будет разобран заново с этой границей, она сохранится и при экспорте.
          </p>
        </DialogHeader>
        <ul className="flex flex-col gap-2">
          {candidates.map((candidate) => (
            <li key={candidate.id} className="flex items-center gap-3 rounded-xl border border-ink-100 p-2">
              <Outline points={candidate.points} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink-800" title={candidate.layer}>
                  {candidate.layer}
                </span>
                <span className="text-xs text-ink-500">{formatArea(candidate.area)}</span>
              </span>
              <Button size="sm" disabled={busy} onClick={() => onChoose(candidate)}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Взять
              </Button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
