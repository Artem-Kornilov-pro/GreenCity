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
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { PageTransition } from "../components/PageTransition";
import { useAuth } from "../context/useAuth";

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: "easeOut" } },
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
    <header className="sticky top-0 z-40 border-b border-ink-200/60 bg-ink-50/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <div className="flex items-center gap-2 font-semibold text-ink-900">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Leaf className="h-4.5 w-4.5" />
          </span>
          GreenCity
        </div>
        <nav className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to="/login">Войти</Link>
          </Button>
          <Button asChild size="sm">
            <Link to="/register">
              Начать бесплатно
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </nav>
      </div>
    </header>
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
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-8 px-6 pb-24 pt-20 text-center md:pt-28">
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3.5 py-1 text-xs font-medium text-brand-700"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Генеративное озеленение из чертежа DXF
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
            className="max-w-3xl text-4xl font-bold tracking-tight text-ink-900 sm:text-5xl md:text-6xl"
          >
            От чертежа двора до готового плана озеленения —{" "}
            <span className="text-brand-600">за минуты, а не недели</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="max-w-2xl text-lg text-ink-500"
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
            <Button asChild size="lg">
              <Link to={session ? "/projects" : "/register"}>
                {session ? "Мои проекты" : "Начать бесплатно"}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link to="/editor">Попробовать без регистрации</Link>
            </Button>
          </motion.div>
        </div>
      </section>

      {/* Как это работает */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <motion.div variants={stagger} initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} className="text-center">
          <motion.h2 variants={fadeUp} className="text-3xl font-bold text-ink-900">
            Как это работает
          </motion.h2>
          <motion.p variants={fadeUp} className="mx-auto mt-3 max-w-xl text-ink-500">
            Три шага между сырым чертежом участка и готовым, проверенным по нормам планом
          </motion.p>
        </motion.div>

        <motion.div
          variants={stagger}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.2 }}
          className="mt-14 grid gap-8 md:grid-cols-3"
        >
          {STEPS.map((step, i) => (
            <motion.div key={step.title} variants={fadeUp} className="relative flex flex-col items-center text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 text-white shadow-soft">
                <step.icon className="h-6 w-6" />
              </div>
              <div className="mt-2 text-xs font-semibold text-brand-600">Шаг {i + 1}</div>
              <h3 className="mt-1 text-lg font-semibold text-ink-900">{step.title}</h3>
              <p className="mt-2 text-sm text-ink-500">{step.text}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      {/* Возможности */}
      <section className="border-y border-ink-200/60 bg-white py-20">
        <div className="mx-auto max-w-6xl px-6">
          <motion.div variants={stagger} initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} className="text-center">
            <motion.h2 variants={fadeUp} className="text-3xl font-bold text-ink-900">
              Всё для полноценного проекта озеленения
            </motion.h2>
          </motion.div>

          <motion.div
            variants={stagger}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.15 }}
            className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3"
          >
            {FEATURES.map((f) => (
              <motion.div key={f.title} variants={fadeUp}>
                <Card className="h-full transition-shadow hover:shadow-soft-lg">
                  <CardContent className="flex flex-col gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100 text-brand-700">
                      <f.icon className="h-5 w-5" />
                    </div>
                    <h3 className="font-semibold text-ink-900">{f.title}</h3>
                    <p className="text-sm text-ink-500">{f.text}</p>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-4xl px-6 py-24 text-center">
        <motion.div initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.4 }} variants={fadeUp}>
          <h2 className="text-3xl font-bold text-ink-900">Готовы озеленить свой двор?</h2>
          <p className="mx-auto mt-3 max-w-lg text-ink-500">
            Аккаунт бесплатный, почта не нужна — только имя пользователя и пароль.
          </p>
          <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Button asChild size="lg">
              <Link to={session ? "/projects" : "/register"}>
                {session ? "Мои проекты" : "Создать аккаунт"}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild variant="ghost" size="lg">
              <Link to="/editor">Или сразу в редактор</Link>
            </Button>
          </div>
        </motion.div>
      </section>

      <footer className="border-t border-ink-200/60 py-8 text-center text-sm text-ink-400">
        GreenCity — инструмент генеративного озеленения дворов.
      </footer>
    </div>
    </PageTransition>
  );
}
