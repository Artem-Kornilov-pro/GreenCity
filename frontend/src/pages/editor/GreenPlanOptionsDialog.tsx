import { useMemo, useState } from "react";
import { Play, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { DEFAULT_GREENPLAN_OPTIONS, type GreenPlanOptions } from "../../api";
import type { CatalogItem } from "../../catalog";

const SECTION_TITLE = "mb-2 text-xs font-semibold uppercase tracking-wide text-ink-400";
const STYLES: { value: GreenPlanOptions["style"]; label: string; hint: string }[] = [
  { value: "auto", label: "Авто", hint: "по похожим реализованным проектам" },
  { value: "regular", label: "Регулярный", hint: "строгая геометрия: сетки, боскеты, диагонали" },
  { value: "landscape", label: "Пейзажный", hint: "свободные формы: рощи, волны, разброс" },
];
// Сколько подходящих видов показывать под строкой поиска -- весь каталог
// (больше сотни деревьев) в диалоге не нужен, нужен поиск.
const MAX_SUGGESTIONS = 40;

function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start gap-2.5 py-1 text-sm ${disabled ? "cursor-not-allowed text-ink-300" : "cursor-pointer text-ink-800"}`}>
      <input
        type="checkbox"
        className="mt-0.5 h-4 w-4 accent-brand-600"
        checked={checked && !disabled}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        {label}
        {hint && <span className="block text-xs text-ink-400">{hint}</span>}
      </span>
    </label>
  );
}

// Выбор предпочтительных видов: выбранные -- плашками, остальные -- поиском.
function SpeciesPicker({
  title,
  items,
  selected,
  onChange,
  disabled,
}: {
  title: string;
  items: CatalogItem[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const suggestions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return items.filter((i) => !selected.includes(i.id) && i.label.toLowerCase().includes(q)).slice(0, MAX_SUGGESTIONS);
  }, [items, query, selected]);

  return (
    <div className={disabled ? "pointer-events-none opacity-40" : ""}>
      <p className="mb-1.5 text-sm font-medium text-ink-700">{title}</p>
      {selected.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selected.map((id) => (
            <span key={id} className="flex items-center gap-1 rounded-full bg-brand-100 py-0.5 pl-2.5 pr-1 text-xs text-brand-800">
              {byId.get(id)?.label ?? id}
              <button
                type="button"
                className="rounded-full p-0.5 hover:bg-brand-200"
                onClick={() => onChange(selected.filter((s) => s !== id))}
                aria-label="Убрать"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <Input placeholder="Начните вводить название вида" value={query} onChange={(e) => setQuery(e.target.value)} />
      {suggestions.length > 0 && (
        <div className="scrollbar-thin mt-1 max-h-36 overflow-y-auto rounded-lg border border-ink-200">
          {suggestions.map((item) => (
            <button
              key={item.id}
              type="button"
              className="block w-full px-3 py-1.5 text-left text-sm text-ink-700 hover:bg-brand-50"
              onClick={() => {
                onChange([...selected, item.id]);
                setQuery("");
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Параметры GreenPlan перед запуском (backend/greenplan/options.py). Всё,
// кроме выбора, считает сервер: предпочтительные виды проходят те же нормы,
// что и остальные, -- почему вид не использован, покажет панель GreenPlan.
export function GreenPlanOptionsDialog({
  open,
  onOpenChange,
  catalog,
  initial,
  busy,
  onRun,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalog: CatalogItem[];
  initial: GreenPlanOptions;
  busy: boolean;
  onRun: (options: GreenPlanOptions) => void;
}) {
  const [options, setOptions] = useState<GreenPlanOptions>(initial);
  const set = <K extends keyof GreenPlanOptions>(key: K, value: GreenPlanOptions[K]) => setOptions((prev) => ({ ...prev, [key]: value }));

  // Только конкретные виды (species_*), а не обобщённые модели.
  const trees = useMemo(() => catalog.filter((i) => i.category === "tree" && i.id.startsWith("species_")), [catalog]);
  const bushes = useMemo(
    () => catalog.filter((i) => i.category === "bush" && i.object_type === "bush" && i.id.startsWith("species_")),
    [catalog],
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setOptions(initial);
        onOpenChange(next);
      }}
    >
      <DialogContent className="scrollbar-thin max-h-[88vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Параметры GreenPlan</DialogTitle>
          <p className="text-sm text-ink-500">Нормы отступов и ассортимент соблюдаются при любых параметрах.</p>
        </DialogHeader>

        <form
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            onRun(options);
          }}
        >
          <section>
            <h3 className={SECTION_TITLE}>Стиль участка</h3>
            <div className="grid grid-cols-3 gap-2">
              {STYLES.map((s) => (
                <button
                  key={s.value}
                  type="button"
                  onClick={() => set("style", s.value)}
                  className={`rounded-xl border px-3 py-2 text-left transition-colors ${
                    options.style === s.value ? "border-brand-500 bg-brand-50" : "border-ink-200 hover:border-ink-300"
                  }`}
                >
                  <span className="block text-sm font-medium text-ink-900">{s.label}</span>
                  <span className="block text-xs text-ink-500">{s.hint}</span>
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3 className={SECTION_TITLE}>Что сажать</h3>
            <div className="flex flex-wrap gap-x-6">
              <Toggle checked={options.trees} onChange={(v) => set("trees", v)} label="Деревья" />
              <Toggle checked={options.bushes} onChange={(v) => set("bushes", v)} label="Кустарники" />
              <Toggle checked={options.lawn} onChange={(v) => set("lawn", v)} label="Газон" />
            </div>
          </section>

          <section>
            <h3 className={SECTION_TITLE}>Существующие насаждения</h3>
            <Toggle
              checked={options.remove_violating_plants}
              onChange={(v) => set("remove_violating_plants", v)}
              label="Убрать деревья и кусты с нарушением норм"
              hint="из исходного чертежа — те, что ближе нормативного отступа к зданиям, сетям и дорогам; их место засаживается заново"
            />
          </section>

          <section className="flex flex-col gap-3">
            <h3 className={SECTION_TITLE}>Предпочтительные виды</h3>
            <p className="-mt-2 text-xs text-ink-500">
              Ставятся первыми везде, где это допускают нормы и ассортимент для этого типа участка.
            </p>
            <SpeciesPicker
              title="Деревья"
              items={trees}
              selected={options.preferred_trees}
              onChange={(ids) => set("preferred_trees", ids)}
              disabled={!options.trees}
            />
            <SpeciesPicker
              title="Кустарники"
              items={bushes}
              selected={options.preferred_bushes}
              onChange={(ids) => set("preferred_bushes", ids)}
              disabled={!options.bushes}
            />
          </section>

          <section>
            <h3 className={SECTION_TITLE}>Благоустройство</h3>
            <Toggle
              checked={options.paths}
              onChange={(v) => set("paths", v)}
              label="Дорожки"
              hint="от подъездов к подъездам и к парковкам, во дворах участка"
            />
            <Toggle
              checked={options.lighting}
              onChange={(v) => set("lighting", v)}
              label="Освещение"
              hint="фонари вдоль новых и существующих дорожек, не ближе 4 м к деревьям"
            />
            <Toggle
              checked={options.benches}
              onChange={(v) => set("benches", v)}
              label="Скамейки и урны"
              hint={options.paths ? "вдоль новых дорожек" : "нужны новые дорожки"}
              disabled={!options.paths}
            />
          </section>

          <div className="flex items-center justify-between gap-2 border-t border-ink-100 pt-4">
            <Button type="button" variant="ghost" onClick={() => setOptions(DEFAULT_GREENPLAN_OPTIONS)}>
              Сбросить
            </Button>
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                Отмена
              </Button>
              <Button
                type="submit"
                disabled={
                  busy ||
                  (!options.trees && !options.bushes && !options.lawn && !options.remove_violating_plants && !options.paths && !options.lighting)
                }
              >
                <Play className="h-4 w-4" />
                Запустить
              </Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
