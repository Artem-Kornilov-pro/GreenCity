import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, X } from "lucide-react";
import { Button } from "../../components/ui/button";

// Выезжающая справа панель (Ассистент, GreenPlan): сцена остаётся видна.
// Ручка прикреплена к левому краю панели и видна, когда панель закрыта.
// handleTop разводит ручки двух панелей по высоте; accent -- зелёная ручка.
export function SlidePanel({
  open,
  onToggle,
  onClose,
  icon,
  title,
  handleTop = "top-1/2",
  accent = false,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  icon: ReactNode;
  title: string;
  handleTop?: string;
  accent?: boolean;
  children: ReactNode;
}) {
  return (
    <motion.aside
      initial={false}
      animate={{ x: open ? 0 : "100%" }}
      transition={{ type: "spring", stiffness: 320, damping: 34 }}
      // Почти белая подложка -- текст читается поверх газона. Открытая панель
      // выше закрытой, чтобы ручка закрытой не ложилась поверх.
      className={`absolute right-0 top-14 ${open ? "z-[31]" : "z-30"} flex h-[calc(100%-3.5rem)] w-full max-w-md flex-col border-l border-ink-200/60 bg-white/90 shadow-soft-lg backdrop-blur-xl backdrop-saturate-150`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={open ? `Свернуть: ${title}` : `Развернуть: ${title}`}
        title={title}
        className={`absolute -left-8 ${handleTop} flex h-16 w-8 -translate-y-1/2 items-center justify-center rounded-l-xl border border-r-0 shadow-soft transition-colors ${
          accent
            ? "border-brand-700 bg-brand-600 text-white hover:bg-brand-700"
            : "border-ink-200/60 bg-white/90 text-brand-600 backdrop-blur-xl hover:bg-white"
        }`}
      >
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.25 }}>
          <ChevronLeft className="h-4 w-4" />
        </motion.span>
      </button>

      <div className="flex items-center justify-between border-b border-ink-200/70 px-4 py-3">
        <div className="flex items-center gap-2 font-semibold text-ink-900">
          {icon}
          {title}
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {children}
    </motion.aside>
  );
}
