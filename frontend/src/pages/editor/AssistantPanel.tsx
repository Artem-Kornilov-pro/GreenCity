import { Loader2, Send, Sparkles } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Textarea } from "../../components/ui/input";
import type { ChatMessage } from "./editorTypes";
import { SlidePanel } from "./SlidePanel";

// Правка плана текстом (/api/edit-with-text): чат с моделью, под каждым
// ответом -- что отклонено и какие предупреждения (координаты считает
// планировщик на бэкенде, модель выбирает только намерение).
export function AssistantPanel({
  open,
  onToggle,
  onClose,
  messages,
  editing,
  instruction,
  onInstructionChange,
  onSubmit,
  sceneLoaded,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  messages: ChatMessage[];
  editing: boolean;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onSubmit: () => void;
  sceneLoaded: boolean;
}) {
  return (
    <SlidePanel open={open} onToggle={onToggle} onClose={onClose} icon={<Sparkles className="h-4.5 w-4.5 text-brand-600" />} title="Ассистент">
      <div className="scrollbar-thin flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-sm text-ink-400">
            <Sparkles className="h-8 w-8 text-brand-300" />
            <p>Опишите на русском, что изменить в плане — например «посади 5 деревьев вдоль южного дома».</p>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={
              m.role === "user"
                ? "max-w-[85%] self-end rounded-2xl rounded-br-sm bg-brand-600 px-3.5 py-2 text-sm text-white"
                : m.role === "error"
                  ? "max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-danger-500/10 px-3.5 py-2 text-sm text-danger-500"
                  : "max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-ink-100 px-3.5 py-2 text-sm text-ink-800"
            }
          >
            <p>{m.text}</p>
            {((m.rejected && m.rejected.length > 0) || (m.warnings && m.warnings.length > 0)) && (
              <div className="mt-1.5 flex flex-col gap-1 border-t border-ink-900/10 pt-1.5 text-xs opacity-80">
                {m.rejected?.map((r, i) => (
                  <p key={`r-${i}`}>✕ {r}</p>
                ))}
                {m.warnings?.map((w, i) => (
                  <p key={`w-${i}`}>⚠ {w}</p>
                ))}
              </div>
            )}
          </div>
        ))}
        {editing && (
          <div className="max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-ink-100 px-3.5 py-2 text-sm text-ink-500">Думаю…</div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        className="flex items-end gap-2 border-t border-ink-200/70 p-3"
      >
        <Textarea
          rows={2}
          placeholder="Например: убери лавки у парковки"
          value={instruction}
          disabled={editing || !sceneLoaded}
          onChange={(e) => onInstructionChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          className="flex-1 resize-none"
        />
        <Button type="submit" size="icon" disabled={editing || !sceneLoaded || !instruction.trim()}>
          {editing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </form>
    </SlidePanel>
  );
}
