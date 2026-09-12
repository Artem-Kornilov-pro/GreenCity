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
    text: "DXF-план участка с границами, зданиями, коммуникациями и парковками разбирается автоматически, слой за слоем.",
  },
  {
    icon: Sparkles,
    title: "Опишите словами или сгенерируйте",
    text: "«Посади деревья вдоль дорожек» — и планировщик сам расставит объекты, соблюдая нормативные отступы. Или нажмите одну кнопку для полного дизайна двора.",
  },
  {
    icon: Download,
    title: "Заберите готовый план",
    text: "Итоговая раскладка экспортируется обратно в DXF — тот же формат, с которого всё начиналось, готовый к дальнейшей работе в CAD.",
  },
];

const FEATURES = [
  {
    icon: Trees,
    title: "Деревья, кусты, газон",
    text: "Автогенерация озеленения по сетке — с группировкой кустов и сплошным газоном на свободной площади.",
  },
  {
    icon: ShieldCheck,
    title: "Нормативные отступы",
    text: "Каждая посадка проверяется по СНиП 2.07.01-89*/СП 42.13330.2016 — от зданий, сетей, дорожек и друг друга.",
  },
  {
    icon: Sparkles,
    title: "Правка текстом на русском",
    text: "Больше 20 операций — от «убери лавки у парковки» до полного дизайна двора одной фразой.",
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
    title: "Экспорт обратно в DXF",
    text: "Итоговый план — не картинка, а полноценный чертёж со слоями, готовый к печати и дальнейшей работе.",
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
    <div className="min-h-full bg-ink-50">
      <Header />

      {/* Hero */}
      <section className="relative overflow-hidden">
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
            Генеративное озеленение из чертежа DXF
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
            className="max-w-6xl text-3xl font-bold tracking-tight text-ink-900 sm:text-4xl md:text-5xl"
          >
            От сырого чертежа двора до полностью готового, проверенного по всем нормативам плана озеленения —{" "}
            <span className="text-brand-600">за считанные минуты, а не за недели ручной работы в CAD-редакторе</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="max-w-4xl text-lg text-ink-500 md:text-xl"
          >
            Загрузите DXF своего участка, опишите словами, что нужно посадить, и получите
            проверенный по нормативам план — с деревьями, кустами, дорожками и МАФ — обратно
            в формате DXF.
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
      <section className="mx-auto max-w-6xl px-6 py-24">
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
          className="mt-16 grid gap-10 md:grid-cols-3"
        >
          {STEPS.map((step, i) => (
            <motion.div key={step.title} variants={fadeUp} className="relative flex flex-col items-center text-center">
              <motion.div
                variants={popIn}
                whileHover={{ scale: 1.12, rotate: 8 }}
                transition={{ type: "spring", stiffness: 260, damping: 14 }}
                className="flex h-20 w-20 items-center justify-center rounded-2xl bg-brand-600 text-white shadow-soft"
              >
                <step.icon className="h-8 w-8" />
              </motion.div>
              <div className="mt-3 text-sm font-semibold text-brand-600">Шаг {i + 1}</div>
              <h3 className="mt-1 text-xl font-semibold text-ink-900">{step.title}</h3>
              <p className="mt-2 text-base text-ink-500">{step.text}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      {/* Возможности */}
      <section className="border-y border-ink-200/60 bg-white py-24">
        <div className="mx-auto max-w-6xl px-6">
          <motion.div variants={stagger} initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} className="text-center">
            <motion.h2 variants={fadeUp} className="text-4xl font-bold text-ink-900 md:text-5xl">
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
                    <p className="text-base text-ink-500">{f.text}</p>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* CTA */}
      <section className="relative mx-auto max-w-4xl px-6 py-28 text-center">
        <motion.div initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.4 }} variants={fadeUp}>
          <h2 className="text-4xl font-bold text-ink-900 md:text-5xl">Готовы озеленить свой двор?</h2>
          <p className="mx-auto mt-4 max-w-lg text-lg text-ink-500">
            Аккаунт бесплатный, почта не нужна — только имя пользователя и пароль.
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
      </section>

      <footer className="border-t border-ink-200/60 py-8 text-center text-base text-ink-400">
        GreenCity — инструмент генеративного озеленения дворов.
      </footer>
    </div>
    </PageTransition>
  );
}
