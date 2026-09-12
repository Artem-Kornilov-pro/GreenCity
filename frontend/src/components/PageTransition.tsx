import type { ReactNode } from "react";
import { motion } from "framer-motion";

// Общая анимация появления/исчезновения для каждой страницы -- используется
// вместе с <AnimatePresence mode="wait"> в App.tsx, чтобы переход между /,
// /login, /projects, /editor не был резким переключением DOM, а выглядел
// одним связным SPA, а не набором отдельных страниц.
export function PageTransition({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      transition={{ duration: 0.22, ease: "easeInOut" }}
      className="min-h-full"
    >
      {children}
    </motion.div>
  );
}
