import type { GreenPlanGenerateResult } from "../../api";

export function makeId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

// Зона выделения мышкой (issue "Выделение участка карты мышкой") -- тип
// строго "selection", это то, по чему backend (text_editor/prompt.py::_build_context)
// узнаёт её в scene.restrictions и отдаёт модели как selected_areas, отдельно
// от обычных зон плана. severity "allowed" -- сама по себе ничего не
// запрещает (Placer.region() её игнорирует), это просто именованный маркер.
export const SELECTION_ZONE_TYPE = "selection";
export const SELECTION_ZONE_NAME = "Выделение";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  applied?: string[];
  rejected?: string[];
  warnings?: string[];
}

// generateGreenPlan (быстро, без LLM) отдаёт всё, кроме report/report_error --
// те приходят отдельным запросом (fetchGreenPlanReport) и домешиваются в это
// же состояние по готовности, см. handleGreenPlan в EditorPage.
export interface GreenPlanState extends GreenPlanGenerateResult {
  report: string | null;
  report_error: string | null;
}
