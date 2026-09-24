# GreenCity frontend

React 19 + TypeScript + Vite, 3D-сцена на react-three-fiber. Общая
архитектура проекта, эндпоинты и GreenPlan — [../docs/TECHNICAL_OVERVIEW.md](../docs/TECHNICAL_OVERVIEW.md),
3D-модели и как подключить свой пак — [public/models/README.md](public/models/README.md).

## Команды

```bash
npm install
npm run dev              # dev-сервер на http://localhost:5173 (бэкенд -- http://localhost:8000)
npx tsc -b --noEmit      # проверка типов (есть в CI)
npx oxlint --deny-warnings
npm run build            # production-сборка в dist/
```

## Устройство `src/`

| Папка / файл | Что внутри |
|---|---|
| `pages/` | Лендинг, вход/регистрация, список проектов |
| `pages/editor/` | Редактор: `EditorPage.tsx` (состояние и обработчики) и его части — `EditorTopBar`, `EditorSidebar` (каталог, выбранный объект, легенда), `AssistantPanel` (правка текстом), `GreenPlanPanel`, общая выезжающая панель `SlidePanel`, `SaveAsDialog`, `StatusBanners` |
| `scene/` | 3D-сцена: здания, зоны ограничений, газон GreenPlan (`Lawns.tsx`), объекты (с GPU-инстансингом повторяющихся моделей), выделение области мышкой, камера |
| `api.ts`, `auth.ts`, `catalog.ts` | Запросы к бэкенду, токены, каталог видов |
| `geometry.ts`, `setbackNorms.ts` | Проверка нарушений отступов при перетаскивании; `setbackNorms.ts` — копия `backend/core/setback_norms.py` (синхронизируется вручную) |
| `types.ts` | Формат сцены — тот же, что `backend/core/schemas.py` |
| `components/ui/` | Кнопки, карточки, диалоги, меню |

Страница редактора загружается лениво (`App.tsx`): three.js не попадает в
основной бандл для посетителей лендинга.
