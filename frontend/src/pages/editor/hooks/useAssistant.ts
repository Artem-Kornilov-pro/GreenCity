import { useCallback, useState } from "react";
import { editWithText, type ChatTurn, type GreenPlanOptions } from "../../../api";
import { errorMessage } from "../../../lib/http";
import type { Scene } from "../../../types";
import { makeId, type AiEditSnapshot, type ChatMessage } from "../editorTypes";

// Сколько прошлых правок чата отправлять модели и сколько id новых объектов
// у каждой (бэкенд режет так же: text_editor/llm_client.py).
const MAX_HISTORY_TURNS = 4;
const MAX_HISTORY_IDS = 60;

// Прошлые правки этого чата -- чтобы модель понимала «убери их», «там же»;
// отменённые не в счёт.
function chatHistory(messages: ChatMessage[]): ChatTurn[] {
  return messages
    .filter((m) => m.role === "assistant" && m.instruction && !m.undone)
    .slice(-MAX_HISTORY_TURNS)
    .map((m) => ({
      instruction: m.instruction ?? "",
      explanation: m.text,
      applied: (m.applied ?? []).slice(0, 4),
      added_ids: (m.addedIds ?? []).slice(0, MAX_HISTORY_IDS),
    }));
}

// Ассистент: правка плана текстом через LLM, отмена последней правки,
// повтор просьбы, на которой запрос упал.
export function useAssistant({
  scene,
  replaceScene,
  runGreenPlan,
  onGreenPlanUndone,
}: {
  scene: Scene | null;
  replaceScene: (scene: Scene) => void;
  // Модель выбрала озеленение GreenPlan -- запустить его на сцене ответа.
  runGreenPlan: (options: GreenPlanOptions, baseScene: Scene) => Promise<Scene | null | undefined>;
  onGreenPlanUndone: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [editing, setEditing] = useState(false);
  const [lastEdit, setLastEdit] = useState<AiEditSnapshot | null>(null);

  // echo=false -- повтор: сообщение пользователя уже в чате.
  const send = useCallback(
    async (text: string, echo: boolean) => {
      if (!scene) return;
      const before = scene;
      if (echo) setMessages((prev) => [...prev, { id: makeId(), role: "user", text }]);
      setEditing(true);
      try {
        const result = await editWithText(scene, text, chatHistory(messages));
        replaceScene(result.scene);
        const messageId = makeId();
        setMessages((prev) => [
          ...prev,
          {
            id: messageId,
            role: "assistant",
            instruction: text,
            text: result.explanation || `Применено изменений: ${result.applied.length}`,
            applied: result.applied,
            rejected: result.rejected,
            warnings: result.warnings,
            addedIds: result.added_ids ?? [],
          },
        ]);
        let after = result.scene;
        if (result.greenplan) after = (await runGreenPlan(result.greenplan, result.scene)) ?? result.scene;
        // Отменять нечего, если план не изменился (вопрос, «не получилось»).
        const changed = result.applied.length > 0 || Boolean(result.greenplan);
        setLastEdit(changed ? { messageId, before, after, ranGreenPlan: Boolean(result.greenplan) } : null);
      } catch (e) {
        setMessages((prev) => [...prev, { id: makeId(), role: "error", text: errorMessage(e), instruction: text }]);
      } finally {
        setEditing(false);
      }
    },
    [scene, messages, replaceScene, runGreenPlan],
  );

  const submit = useCallback(() => {
    const text = instruction.trim();
    if (!scene || !text) return;
    setInstruction("");
    void send(text, true);
  }, [scene, instruction, send]);

  // «Повторить» у ошибки: та же просьба ещё раз, ошибка из чата убирается.
  const retry = useCallback(
    (messageId: string) => {
      const failed = messages.find((m) => m.id === messageId && m.role === "error");
      if (!failed?.instruction) return;
      setMessages((prev) => prev.filter((m) => m.id !== messageId));
      void send(failed.instruction, false);
    },
    [messages, send],
  );

  // Отмена последней правки: план возвращается к виду до неё. Доступна, пока
  // план после правки не менялся -- иначе отмена стёрла бы и ручные правки.
  const undoable = lastEdit !== null && scene === lastEdit.after;
  const undo = useCallback(() => {
    if (!lastEdit || scene !== lastEdit.after) return;
    replaceScene(lastEdit.before);
    setMessages((prev) => prev.map((m) => (m.id === lastEdit.messageId ? { ...m, undone: true } : m)));
    if (lastEdit.ranGreenPlan) onGreenPlanUndone();
    setLastEdit(null);
  }, [lastEdit, scene, replaceScene, onGreenPlanUndone]);

  return {
    instruction,
    setInstruction,
    messages,
    editing,
    submit,
    retry,
    undo,
    undoableMessageId: undoable ? lastEdit.messageId : null,
  };
}
