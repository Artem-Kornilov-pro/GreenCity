import { lazy, Suspense } from "react";
import { AnimatePresence } from "framer-motion";
import { Route, Routes, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import LandingPage from "./pages/LandingPage";
import AuthPage from "./pages/AuthPage";
import ProjectsPage from "./pages/ProjectsPage";

// three.js/@react-three/fiber/@react-three/drei -- самые тяжёлые зависимости
// в package.json -- тянутся только сценой редактора (SceneView.tsx и всё под
// scene/). Без lazy() они грузились бы в основной бандл даже для посетителя
// лендинга, который 3D вообще не увидит.
const EditorPage = lazy(() => import("./pages/editor/EditorPage"));
// Проект по ссылке -- та же 3D-сцена, только просмотр.
const SharedProjectPage = lazy(() => import("./pages/SharedProjectPage"));

function EditorFallback() {
  return (
    <div className="flex h-screen items-center justify-center bg-ink-50">
      <Loader2 className="h-6 w-6 animate-spin text-ink-400" />
    </div>
  );
}

export default function App() {
  const location = useLocation();
  // Новый и сохранённый проект в редакторе -- одна страница: после «Сохранить
  // как…» адрес меняется на /editor/<id>, а результат GreenPlan и его файлы
  // остаются (иначе смена ключа пересоздала бы страницу).
  const routeKey = location.pathname.startsWith("/editor") ? "/editor" : location.pathname;

  return (
    <AnimatePresence mode="wait">
      <Routes location={location} key={routeKey}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/register" element={<AuthPage mode="register" />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route
          path="/share/:token"
          element={
            <Suspense fallback={<EditorFallback />}>
              <SharedProjectPage />
            </Suspense>
          }
        />
        <Route
          path="/editor"
          element={
            <Suspense fallback={<EditorFallback />}>
              <EditorPage />
            </Suspense>
          }
        />
        <Route
          path="/editor/:projectId"
          element={
            <Suspense fallback={<EditorFallback />}>
              <EditorPage />
            </Suspense>
          }
        />
      </Routes>
    </AnimatePresence>
  );
}
