import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../lib/http";

export interface AsyncTask<A extends unknown[], R> {
  // undefined -- действие упало, текст ошибки в error.
  run: (...args: A) => Promise<R | undefined>;
  // Повторить последний вызов с теми же аргументами (кнопка «Повторить»).
  retry: () => void;
  dismiss: () => void;
  busy: boolean;
  error: string | null;
}

// Асинхронное действие с флагом «идёт», текстом ошибки и повтором. fn может
// замыкаться на текущее состояние: повтор вызывает её свежую версию.
export function useAsyncTask<A extends unknown[], R>(fn: (...args: A) => Promise<R>): AsyncTask<A, R> {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  const argsRef = useRef<A | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (...args: A): Promise<R | undefined> => {
    argsRef.current = args;
    setBusy(true);
    setError(null);
    try {
      return await fnRef.current(...args);
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);

  const retry = useCallback(() => {
    if (argsRef.current) void run(...argsRef.current);
  }, [run]);

  const dismiss = useCallback(() => setError(null), []);

  return { run, retry, dismiss, busy, error };
}
