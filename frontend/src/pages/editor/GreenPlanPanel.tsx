import { Download, Loader2, Trees } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import type { GreenPlanViolation } from "../../api";
import type { GreenPlanState } from "./editorTypes";
import { SlidePanel } from "./SlidePanel";

const SECTION_TITLE = "mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400";
const STYLE_LABELS: Record<string, string> = { regular: "регулярный", landscape: "пейзажный" };

// Результат GreenPlan: без чат-формы, только отчёт по уже готовой расстановке --
// пояснительная записка, обоснование (LLM), нарушения норм и ведомость.
export function GreenPlanPanel({
  open,
  onToggle,
  onClose,
  result,
  reportLoading,
  documentBusy,
  onDownloadDocument,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  result: GreenPlanState | null;
  reportLoading: boolean;
  documentBusy: boolean;
  onDownloadDocument: () => void;
}) {
  const existingLawnSqm = (result?.scene.lawns ?? []).filter((l) => l.status === "existing").reduce((sum, l) => sum + l.area_sqm, 0);
  const [showExisting, setShowExisting] = useState(false);
  const [newViolations, existingViolations] = useMemo(() => {
    if (!result) return [[], []];
    const generated = new Set(result.scene.objects.filter((o) => o.metadata?.generated === true).map((o) => o.id));
    const own: GreenPlanViolation[] = [];
    const inherited: GreenPlanViolation[] = [];
    for (const v of result.violations) (generated.has(v.object_id) ? own : inherited).push(v);
    return [own, inherited];
  }, [result]);
  return (
    <SlidePanel open={open} onToggle={onToggle} onClose={onClose} icon={<Trees className="h-4.5 w-4.5 text-brand-600" />} title="GreenPlan">
      <div className="scrollbar-thin flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
        {!result && (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-sm text-ink-400">
            <Trees className="h-8 w-8 text-brand-300" />
            <p>Нажмите «GreenPlan» на панели инструментов — участок озеленится по аналогии с похожими прошлыми проектами.</p>
          </div>
        )}

        {result && (
          <>
            {/* Пояснительная записка -- "сценарий выгрузки документации" из ТЗ.
                Можно и без текста от LLM: он идёт только приложением. */}
            <Button size="sm" variant="outline" onClick={onDownloadDocument} disabled={documentBusy}>
              {documentBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Пояснительная записка (DOCX)
            </Button>

            {/* Общее решение на участок -- до решений по зонам: сначала стиль,
                потом приёмы в этом стиле (greenplan/pattern_assignment.py). */}
            {result.assignments[0] && (
              <section>
                <h3 className={SECTION_TITLE}>Стиль участка</h3>
                <p className="text-sm text-ink-700">
                  {result.assignments[0].site_style
                    ? `${STYLE_LABELS[result.assignments[0].site_style]}${result.assignments[0].lead_project ? `, ведущий аналог — ${result.assignments[0].lead_project}` : ""}`
                    : "не определён — у похожих проектов нет решений определённого стиля"}
                </p>
              </section>
            )}

            {/* Что из параметров GreenPlan не удалось выполнить и почему
                (вид не по нормам, опоры не встают в охранных зонах и т.п.). */}
            {(result.notes ?? []).length > 0 && (
              <section>
                <h3 className={SECTION_TITLE}>Замечания к параметрам</h3>
                <ul className="flex flex-col gap-1.5">
                  {result.notes.map((note) => (
                    <li key={note} className="rounded-xl bg-warning-500/15 px-3 py-2 text-xs text-ink-700">
                      {note}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h3 className={SECTION_TITLE}>Обоснование</h3>
              {reportLoading ? (
                <p className="flex items-center gap-2 rounded-2xl bg-ink-100 px-3.5 py-2.5 text-sm text-ink-500">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Генерирую объяснение (обычно около 30 секунд)…
                </p>
              ) : result.report ? (
                <p className="whitespace-pre-line rounded-2xl bg-ink-100 px-3.5 py-2.5 text-sm text-ink-800">{result.report}</p>
              ) : (
                <p className="rounded-2xl bg-warning-500/15 px-3.5 py-2.5 text-sm text-ink-700">
                  ⚠ Текст-объяснение недоступен: {result.report_error ?? "неизвестная причина"}
                </p>
              )}
            </section>

            {/* Нарушения новой посадки -- проверка работы GreenPlan -- отдельно от
                нарушений объектов, которые уже были в чертеже: на реальном
                проекте (Харьковская) их больше тысячи, генератор их не ставил и
                не трогал, а в общем списке они заслоняли его собственный
                результат. */}
            <section>
              <h3 className={SECTION_TITLE}>Нарушения норм новой посадкой ({newViolations.length})</h3>
              {newViolations.length > 0 ? (
                <ViolationList violations={newViolations} />
              ) : (
                <Badge variant="success">Нарушений нет</Badge>
              )}
              {existingViolations.length > 0 && (
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={() => setShowExisting((v) => !v)}
                    className="text-left text-xs text-ink-500 underline decoration-dotted underline-offset-2 hover:text-ink-700"
                  >
                    {showExisting ? "Скрыть" : "Показать"} нарушения объектов из исходного чертежа ({existingViolations.length})
                  </button>
                  {showExisting && (
                    <div className="mt-1.5">
                      <ViolationList violations={existingViolations} />
                    </div>
                  )}
                </div>
              )}
            </section>

            <section>
              <h3 className={SECTION_TITLE}>Новая посадка по видам и газон</h3>
              {result.assortment.length > 0 ? (
                <div className="flex flex-col gap-1 text-sm text-ink-700">
                  {result.assortment.map((row, i) => (
                    <div key={`${row.category}-${row.species}-${i}`} className="flex items-center justify-between gap-2 rounded-lg bg-white/40 px-3 py-1.5">
                      <span>
                        {row.species} <span className="text-ink-400">({row.category})</span>
                      </span>
                      <span className="font-medium">
                        {row.unit === "м²" ? `${row.count.toLocaleString("ru-RU")} м²` : row.count}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-ink-400">Новых объектов не добавлено.</p>
              )}
              {existingLawnSqm > 0 && (
                <p className="mt-2 text-xs text-ink-500">
                  Существующий газон сохраняется: {Math.round(existingLawnSqm).toLocaleString("ru-RU")} м²
                </p>
              )}
              {/* Основание подбора видов -- из самой расстановки (greenplan/species_selection.py),
                  без LLM: видно и тогда, когда текст-объяснение недоступен. */}
              {[...new Set(result.assignments.map((a) => a.species_basis).filter(Boolean))].map((basis) => (
                <p key={basis} className="mt-2 text-xs text-ink-500">
                  Виды подобраны: {basis}
                </p>
              ))}
            </section>

            {(result.improvements ?? []).length > 0 && (
              <section>
                <h3 className={SECTION_TITLE}>Благоустройство</h3>
                <div className="flex flex-col gap-1 text-sm text-ink-700">
                  {result.improvements.map((row) => (
                    <div key={row.species} className="flex items-center justify-between gap-2 rounded-lg bg-white/40 px-3 py-1.5">
                      <span>{row.species}</span>
                      <span className="font-medium">
                        {row.unit === "м²" ? `${row.count.toLocaleString("ru-RU")} м²` : row.count}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </SlidePanel>
  );
}

function ViolationList({ violations }: { violations: GreenPlanViolation[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {violations.map((v, i) => (
        <Badge key={`${v.object_id}-${v.zone_id}-${i}`} variant={v.severity === "forbidden" ? "danger" : "warning"} className="justify-start">
          ⚠ {v.object_id}: {v.message} (до зоны {v.distance_m} м, нужно {v.required_m} м)
        </Badge>
      ))}
    </div>
  );
}
