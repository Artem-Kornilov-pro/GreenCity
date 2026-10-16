import { useMemo, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import type { LayerInfo, LayerKind } from "../../types";

const SOURCE_LABEL: Record<NonNullable<LayerInfo["source"]>, string> = {
  override: "назначено вами",
  name: "по имени",
  normalized: "по имени",
  synonym: "по синониму",
  content: "по содержимому",
};

const sameMap = (a: Record<string, string>, b: Record<string, string>) =>
  Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([k, v]) => b[k] === v);

interface LayerMappingProps {
  onOpenChange: (open: boolean) => void;
  layers: LayerInfo[];
  warnings: string[];
  kinds: LayerKind[];
  kindsLoading: boolean;
  initialMap: Record<string, string>;
  canReparse: boolean;
  busy: boolean;
  onApply: (layerMap: Record<string, string>) => void;
}

// Слои чертежа: что разбор взял из каждого слоя. Слою, который не распознан
// или распознан не так, назначается тип -- и чертёж разбирается заново.
export function LayerMappingDialog({ open, ...props }: LayerMappingProps & { open: boolean }) {
  return (
    <Dialog open={open} onOpenChange={props.onOpenChange}>
      <DialogContent className="flex max-h-[88vh] max-w-3xl flex-col">
        {/* Содержимое монтируется при каждом открытии: выбор начинается с
            назначений текущей сцены, а не с прошлого незавершённого. */}
        <LayerMappingForm {...props} />
      </DialogContent>
    </Dialog>
  );
}

function LayerMappingForm({ onOpenChange, layers, warnings, kinds, kindsLoading, initialMap, canReparse, busy, onApply }: LayerMappingProps) {
  const [map, setMap] = useState<Record<string, string>>(initialMap);
  const unrecognized = layers.filter((l) => !l.label).length;
  const [onlyUnrecognized, setOnlyUnrecognized] = useState(unrecognized > 0);
  const [query, setQuery] = useState("");

  // Сначала то, что требует внимания: нераспознанные, затем назначенные.
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rank = (l: LayerInfo) => (!l.label ? 0 : l.source === "override" ? 1 : 2);
    return layers
      .filter((l) => (!onlyUnrecognized || !l.label || map[l.name]) && (l.displayName ?? l.name).toLowerCase().includes(q))
      .sort((a, b) => rank(a) - rank(b));
  }, [layers, onlyUnrecognized, map, query]);

  const assign = (layer: string, kind: string) =>
    setMap((prev) => {
      const next = { ...prev };
      if (kind) next[layer] = kind;
      else delete next[layer];
      return next;
    });

  return (
    <>
      <DialogHeader>
        <DialogTitle>Слои чертежа</DialogTitle>
        <p className="text-sm text-ink-500">
          Распознано {layers.length - unrecognized} из {layers.length} слоёв. Если слой не распознан или распознан не так,
          выберите его тип — чертёж будет разобран заново.
        </p>
      </DialogHeader>

      {warnings.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1 rounded-xl bg-warning-500/10 px-3 py-2 text-xs text-ink-700">
          {warnings.map((w) => (
            <li key={w} className="flex gap-1.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning-500" />
              {w}
            </li>
          ))}
        </ul>
      )}

      <div className="mb-2 flex items-center gap-3">
        <Input placeholder="Поиск слоя" value={query} onChange={(e) => setQuery(e.target.value)} className="h-8 max-w-xs text-sm" />
        <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-600"
            checked={onlyUnrecognized}
            onChange={(e) => setOnlyUnrecognized(e.target.checked)}
          />
          Только нераспознанные ({unrecognized})
        </label>
      </div>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto rounded-xl border border-ink-100">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-ink-50 text-left text-xs uppercase tracking-wide text-ink-400">
            <tr>
              <th className="px-3 py-2 font-semibold">Слой</th>
              <th className="px-3 py-2 text-right font-semibold">Объектов</th>
              <th className="px-3 py-2 font-semibold">Что взято</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((layer) => {
              const auto = layer.source === "override" ? null : layer.label;
              return (
                <tr key={layer.name} className="border-t border-ink-100">
                  <td className="max-w-[16rem] px-3 py-1.5">
                    <span className="block truncate text-ink-800" title={layer.name}>
                      {layer.displayName ?? layer.name}
                    </span>
                    {layer.source && layer.label && (
                      <Badge variant={layer.source === "override" ? "brand" : "neutral"} className="mt-0.5">
                        {SOURCE_LABEL[layer.source]}
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink-500">{layer.entities}</td>
                  <td className="px-3 py-1.5">
                    <select
                      aria-label={`Тип слоя ${layer.displayName ?? layer.name}`}
                      className={`w-full rounded-lg border px-2 py-1 text-sm ${
                        map[layer.name] ? "border-brand-400 bg-brand-50" : layer.label ? "border-ink-200 bg-white" : "border-warning-500/50 bg-warning-500/10"
                      }`}
                      value={map[layer.name] ?? ""}
                      disabled={kindsLoading}
                      onChange={(e) => assign(layer.name, e.target.value)}
                    >
                      <option value="">{auto ? `Авто: ${auto}` : layer.source === "override" ? "Авто" : "Не распознан — не учитывается"}</option>
                      {kinds.map((k) => (
                        <option key={k.id} value={k.id}>
                          {k.label}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={3} className="px-3 py-6 text-center text-ink-400">
                  Слоёв не найдено
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 border-t border-ink-100 pt-4">
        <p className="text-xs text-ink-500">
          {canReparse
            ? "Чертёж будет разобран заново: ручные правки и результат GreenPlan сбросятся."
            : "Исходного чертежа на сервере нет — загрузите его заново, чтобы назначить слои."}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="button" disabled={!canReparse || busy || sameMap(map, initialMap)} onClick={() => onApply(map)}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Разобрать заново
          </Button>
        </div>
      </div>
    </>
  );
}
