import type { GreenPlanGenerateResult, GreenPlanOptions } from "../../api";
import type { Scene } from "../../types";

export function makeId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

// Зона, выделенная мышкой: type "selection" -- по нему ассистент отличает её
// от зон плана; severity "allowed" -- ничего не запрещает.
export const SELECTION_ZONE_TYPE = "selection";
export const SELECTION_ZONE_NAME = "Выделение";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  applied?: string[];
  rejected?: string[];
  warnings?: string[];
  // У ответа ассистента: просьба, на которую он ответил (уходит модели как
  // история чата), и отменена ли правка кнопкой "Отменить".
  instruction?: string;
  undone?: boolean;
  // id объектов, созданных этой правкой, -- для отсылок "убери их".
  addedIds?: string[];
}

// Последняя правка ассистента -- для отмены: сцена до неё и после. Отменить
// можно, пока план не менялся после неё (scene === after).
export interface AiEditSnapshot {
  messageId: string;
  before: Scene;
  after: Scene;
  ranGreenPlan: boolean;
}

// Результат GreenPlan; report и report_error приходят отдельным запросом.
export interface GreenPlanState extends GreenPlanGenerateResult {
  // С какими параметрами запускали -- они же уходят в пояснительную записку.
  options: GreenPlanOptions;
  report: string | null;
  report_error: string | null;
}
