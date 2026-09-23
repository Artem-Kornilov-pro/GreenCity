import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, X } from "lucide-react";
import { Button } from "../../components/ui/button";

// Выезжающая справа панель (Ассистент, GreenPlan), а не модальное окно:
// сцена остаётся видна и доступна. Ручка-выдвигалка прикреплена к левому краю
// панели, поэтому, когда панель уезжает вправо (закрыта), ручка выезжает
// вместе с ней и остаётся видна у самого края экрана.
export function SlidePanel({
  open,
  onToggle,
  onClose,
  icon,
  title,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <motion.aside
      initial={false}
      animate={{ x: open ? 0 : "100%" }}
      transition={{ type: "spring", stiffness: 320, damping: 34 }}
      className="absolute right-0 top-14 z-30 flex h-[calc(100%-3.5rem)] w-full max-w-md flex-col border-l border-white/30 bg-white/20 shadow-soft-lg backdrop-blur-xl backdrop-saturate-150"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={open ? `Свернуть: ${title}` : `Развернуть: ${title}`}
        className="absolute -left-8 top-1/2 flex h-16 w-8 -translate-y-1/2 items-center justify-center rounded-l-xl border border-r-0 border-white/30 bg-white/20 text-brand-600 shadow-soft backdrop-blur-xl backdrop-saturate-150 transition-colors hover:bg-white/40"
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
