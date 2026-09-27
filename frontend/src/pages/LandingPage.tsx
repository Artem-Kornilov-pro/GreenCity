import { Link } from "react-router-dom";
import { motion, type Variants } from "framer-motion";
import {
  Leaf,
  UploadCloud,
  Sparkles,
  Download,
  ShieldCheck,
  Trees,
  FolderKanban,
  ArrowRight,
  Ruler,
  ChevronDown,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { PageTransition } from "../components/PageTransition";
import { useAuth } from "../context/useAuth";
import { typograph } from "../lib/typograph";

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 28 },
  show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: "easeOut" } },
};

const popIn: Variants = {
  hidden: { opacity: 0, y: 20, scale: 0.6, rotate: -12 },
  show: { opacity: 1, y: 0, scale: 1, rotate: 0, transition: { duration: 0.55, ease: "easeOut" } },
};

const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.12 } },
};

const STEPS = [
  {
    icon: UploadCloud,
    title: "Загрузите чертёж",
    text: "DXF-файл или папка DWG: границы, здания, сети и парковки разбираются автоматически, слой за слоем.",
  },
  {
    icon: Sparkles,
    title: "Озелените участок",
    text: "Кнопка GreenPlan подберёт посадки по похожим проектам, а ассистент выполнит просьбу вроде «посади липы вдоль дорожек».",
  },
  {
    icon: Download,
    title: "Заберите готовый план",
    text: "Чертёж со слоями — обратно в DXF, а к нему пояснительная записка с ведомостью посадок в DOCX.",
  },
];

const FEATURES = [
  {
    icon: Trees,
    title: "GreenPlan",
    text: "Автоозеленение по 34 похожим проектам: единый стиль участка, виды из ассортимента Москвы, газон на свободной земле.",
  },
  {
    icon: ShieldCheck,
    title: "Нормативные отступы",
    text: "Каждая посадка проверяется по СП 42.13330.2016, 743-ПП и МГСН 1.02-02 — от зданий, сетей, дорожек и друг друга, с учётом породы.",
  },
  {
    icon: Sparkles,
    title: "ИИ-ассистент на русском",
    text: "Больше 20 операций — от «убери лавки у парковки» до озеленения всего участка одной фразой. Последнюю правку можно отменить.",
  },
  {
    icon: Ruler,
    title: "Обход препятствий",
    text: "Дорожка между двумя точками сама обходит здания по кратчайшему пути, а не упирается в стену.",
  },
  {
    icon: FolderKanban,
    title: "Свои проекты",
    text: "До трёх сохранённых планов на аккаунт — вернитесь и продолжите редактирование в любой момент.",
  },
  {
    icon: Download,
    title: "Экспорт в DXF и DOCX",
    text: "Итоговый план — не картинка, а чертёж со слоями и пояснительная записка с ведомостью по ГОСТ 21.508.",
  },
];

function Header() {
  return (
    <motion.header
      initial={{ opacity: 0, y: -24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="sticky top-0 z-40 border-b border-white/60 bg-white/55 shadow-sm backdrop-blur-xl backdrop-saturate-150"
    >
      <div className="mx-auto flex h-20 max-w-6xl items-center justify-between px-6">
        <div className="flex items-center gap-2.5 text-lg font-semibold text-ink-900">
          <motion.span
            whileHover={{ rotate: 12, scale: 1.1 }}
            transition={{ type: "spring", stiffness: 300, damping: 12 }}
            className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-600 text-white"
          >
            <Leaf className="h-5 w-5" />
          </motion.span>
          GreenCity
        </div>
        <nav className="flex items-center gap-2">
          <Button asChild variant="ghost" size="md" className="transition-transform hover:scale-105 active:scale-95">
            <Link to="/login">Войти</Link>
          </Button>
          <Button asChild size="md" className="group transition-transform hover:scale-105 active:scale-95">
            <Link to="/register">
              Начать бесплатно
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </Link>
          </Button>
        </nav>
      </div>
    </motion.header>
  );
}

export default function LandingPage() {
  const { session } = useAuth();

  return (
    <PageTransition>
    <div className="h-screen snap-y snap-mandatory overflow-y-scroll scroll-smooth bg-ink-50">
      <Header />

      {/* Hero */}
      <section className="relative flex min-h-screen scroll-mt-20 snap-start flex-col justify-start overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_60%_50%_at_50%_-10%,theme(colors.brand.100),transparent)]"
        />
        {/* Плавающие декоративные пятна -- чисто орнаментальные, aria-hidden */}
        <motion.div
          aria-hidden
          className="pointer-events-none absolute -left-24 top-10 -z-10 h-72 w-72 rounded-full bg-brand-200/50 blur-3xl"
          animate={{ x: [0, 40, 0], y: [0, -30, 0], scale: [1, 1.15, 1] }}
          transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          aria-hidden
          className="pointer-events-none absolute -right-20 top-40 -z-10 h-80 w-80 rounded-full bg-brand-300/40 blur-3xl"
          animate={{ x: [0, -30, 0], y: [0, 40, 0], scale: [1, 1.1, 1] }}
          transition={{ duration: 14, repeat: Infinity, ease: "easeInOut", delay: 1 }}
        />

        <div className="mx-auto flex max-w-6xl flex-col items-center gap-9 px-6 pb-28 pt-24 text-center md:pt-32">
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="inline-flex items-center gap-2 rounded-full border border-brand-200 bg-brand-50 px-4 py-1.5 text-sm font-medium text-brand-700"
          >
            <motion.span
              animate={{ rotate: [0, 15, -15, 0] }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
            >
              <Sparkles className="h-4 w-4" />
            </motion.span>
            Генеративное озеленение из чертежа DXF и DWG
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
            className="max-w-5xl text-balance text-3xl font-bold leading-tight tracking-tight text-ink-900 sm:text-4xl md:text-5xl"
          >
            {typograph("От чертежа двора до плана озеленения, проверенного по нормативам, —")}{" "}
            <span className="text-brand-600">{typograph("за минуты, а не за недели работы в CAD")}</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="max-w-3xl text-pretty text-lg text-ink-500 md:text-xl"
          >
            {typograph(
              "Загрузите чертёж участка, нажмите GreenPlan или опишите словами, что посадить, — и получите план с деревьями, кустами, газоном, дорожками и МАФ, готовый к экспорту в DXF.",
            )}
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.15 }}
            className="flex flex-col items-center gap-4 sm:flex-row"
          >
            <Button asChild size="lg" className="group h-14 px-8 text-lg transition-transform hover:scale-105 active:scale-95">
              <Link to={session ? "/projects" : "/register"}>
                {session ? "Мои проекты" : "Начать бесплатно"}
                <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
              </Link>
            </Button>
            <Button
              asChild
              variant="outline"
              size="lg"
              className="h-14 px-8 text-lg transition-transform hover:scale-105 active:scale-95"
            >
              <Link to="/editor">Попробовать без регистрации</Link>
            </Button>
          </motion.div>
        </div>

        <motion.div
          aria-hidden
          className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 text-ink-300"
          animate={{ y: [0, 10, 0] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
        >
          <ChevronDown className="h-7 w-7" />
        </motion.div>
      </section>

      {/* Как это работает */}
      <section className="mx-auto flex min-h-screen max-w-6xl scroll-mt-20 snap-start flex-col justify-start px-6 py-24">
        <motion.div variants={stagger} initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} className="text-center">
          <motion.h2 variants={fadeUp} className="text-4xl font-bold text-ink-900 md:text-5xl">
            Как это работает
          </motion.h2>
        </motion.div>

        <motion.div
          variants={stagger}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.2 }}
          className="mt-20 grid gap-10 md:grid-cols-3"
        >
          {STEPS.map((step, i) => (
            <motion.div key={step.title} variants={fadeUp} className="relative flex flex-col items-center text-center">
              <div className="flex h-28 items-center justify-center">
                <motion.div
                  variants={popIn}
                  whileHover={{ scale: 1.12, rotate: 8 }}
                  transition={{ type: "spring", stiffness: 260, damping: 14 }}
                  className="flex h-20 w-20 items-center justify-center rounded-2xl bg-brand-600 text-white shadow-soft"
                >
                  <step.icon className="h-8 w-8" />
                </motion.div>
              </div>
              <div className="mt-3 text-sm font-semibold text-brand-600">Шаг {i + 1}</div>
              <h3 className="mt-1 text-balance text-xl font-semibold text-ink-900">{typograph(step.title)}</h3>
              <p className="mt-2 max-w-sm text-pretty text-base text-ink-500">{typograph(step.text)}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      {/* Возможности */}
      <section className="flex min-h-screen scroll-mt-20 snap-start flex-col justify-start border-y border-ink-200/60 bg-white py-24">
        <div className="mx-auto max-w-6xl px-6">
          <motion.div variants={stagger} initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} className="text-center">
            <motion.h2 variants={fadeUp} className="text-balance text-4xl font-bold text-ink-900 md:text-5xl">
              Всё для полноценного проекта озеленения
            </motion.h2>
          </motion.div>

          <motion.div
            variants={stagger}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.15 }}
            className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
          >
            {FEATURES.map((f) => (
              <motion.div key={f.title} variants={fadeUp} whileHover={{ y: -8 }} transition={{ type: "spring", stiffness: 300, damping: 20 }}>
                <Card className="h-full transition-shadow hover:shadow-soft-lg">
                  <CardContent className="flex flex-col gap-3">
                    <motion.div
                      whileHover={{ rotate: 10, scale: 1.1 }}
                      transition={{ type: "spring", stiffness: 300, damping: 12 }}
                      className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-100 text-brand-700"
                    >
                      <f.icon className="h-6 w-6" />
                    </motion.div>
                    <h3 className="text-lg font-semibold text-ink-900">{f.title}</h3>
                    <p className="text-pretty text-base text-ink-500">{typograph(f.text)}</p>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* CTA -- заголовок на той же высоте, что и у остальных секций (py-24),
          а не посередине экрана: там он стоял заметно ниже них. */}
      <section className="relative flex min-h-screen scroll-mt-20 snap-start flex-col">
        <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col items-center justify-start px-6 pt-24 text-center">
          <motion.div initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.4 }} variants={fadeUp}>
            <h2 className="text-balance text-4xl font-bold text-ink-900 md:text-5xl">Готовы озеленить свой двор?</h2>
            {/* text-balance -- строки примерно равной длины вместо длинной первой
                и одинокого "пароль." на второй; неразрывный пробел не даёт тире
                начать строку. */}
            <p className="mx-auto mt-4 max-w-lg text-balance text-lg text-ink-500">
              Аккаунт бесплатный, почта не нужна&nbsp;— только имя пользователя и пароль.
            </p>
            <div className="relative mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <motion.span
                aria-hidden
                className="pointer-events-none absolute left-1/2 top-1/2 -z-10 h-24 w-56 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-300/40 blur-2xl sm:w-40"
                animate={{ scale: [1, 1.25, 1], opacity: [0.5, 0.9, 0.5] }}
                transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
              />
              <Button asChild size="lg" className="group h-14 px-8 text-lg transition-transform hover:scale-105 active:scale-95">
                <Link to={session ? "/projects" : "/register"}>
                  {session ? "Мои проекты" : "Создать аккаунт"}
                  <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
                </Link>
              </Button>
              <Button
                asChild
                variant="ghost"
                size="lg"
                className="h-14 px-8 text-lg transition-transform hover:scale-105 active:scale-95"
              >
                <Link to="/editor">Или сразу в редактор</Link>
              </Button>
            </div>
          </motion.div>
        </div>

        <footer className="border-t border-ink-200/60 py-8 text-center text-base text-ink-400">
          GreenCity — инструмент генеративного озеленения дворов.
        </footer>
      </section>
    </div>
    </PageTransition>
  );
}
