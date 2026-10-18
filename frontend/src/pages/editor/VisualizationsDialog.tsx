import { Download } from "lucide-react";
import type { GreenPlanVisualizations } from "../../api";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";

const VIEW_LABELS: Record<string, string> = {
  overview: "Общий вид",
  pedestrian: "Вид пешехода",
  detail: "Крупный план посадок",
};

// Иллюстрации участка после озеленения (Alice AI ART, бонусное задание ТЗ):
// рисуются по описанию решений GreenPlan, не по чертежу -- рядом пометка.
export function VisualizationsDialog({
  open,
  onOpenChange,
  result,
  title,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  result: GreenPlanVisualizations | null;
  title: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="scrollbar-thin max-h-[90vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Визуализации участка</DialogTitle>
          {result && <p className="text-sm text-ink-500">{result.note}</p>}
        </DialogHeader>
        <div className="flex flex-col gap-4">
          {result?.visualizations.map((v) => {
            // Alice AI ART отдаёт JPEG; на случай PNG -- по сигнатуре.
            const png = v.image.startsWith("iVBOR");
            const src = `data:image/${png ? "png" : "jpeg"};base64,${v.image}`;
            const label = VIEW_LABELS[v.view] ?? v.view;
            return (
              <figure key={v.view} className="flex flex-col gap-2">
                <img src={src} alt={`${label}: ${v.prompt}`} className="w-full rounded-xl border border-ink-100" />
                <figcaption className="flex items-start justify-between gap-3 text-xs text-ink-500">
                  <span>
                    <span className="font-medium text-ink-700">{label}.</span> {v.prompt}
                  </span>
                  <Button asChild size="sm" variant="outline">
                    <a href={src} download={`Визуализация — ${title} — ${label}.${png ? "png" : "jpg"}`}>
                      <Download className="h-3.5 w-3.5" />
                      JPG
                    </a>
                  </Button>
                </figcaption>
              </figure>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
