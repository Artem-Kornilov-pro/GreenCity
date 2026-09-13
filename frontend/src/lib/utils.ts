import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Стандартная связка для Tailwind-компонентов: clsx собирает условные классы,
// twMerge разруливает конфликты утилит (например когда вызывающий код
// передаёт свой className с другим цветом фона поверх дефолтного).
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
