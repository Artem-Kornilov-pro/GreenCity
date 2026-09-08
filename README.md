# GreenCity

Сервис для автоматического проектирования озеленения города с учётом расположения подземных коммуникаций и городской среды (проект для хакатона ЛЦТ).

## Структура репозитория

```
locations/    6 тестовых DXF-локаций (от одного дома до района на 264 здания)
parser/       DXF -> JSON (parse_dxf.py): границы участка, зоны ограничений, объекты
converters/   GeoJSON / SHP / DWG -> DXF (для внешних источников данных)
backend/      FastAPI: DXF -> JSON, автогенерация озеленения (подробности — backend/README.md)
frontend/     React + TypeScript + react-three-fiber: 3D-просмотр и редактирование
```

## Быстрый старт

### Вариант A: Docker

```bash
docker compose up -d --build
```

Поднимет оба сервиса: backend на `http://localhost:8000`, frontend на `http://localhost:5173`. Код смонтирован volume'ами — правки в `backend/`, `parser/`, `frontend/` подхватываются на лету (uvicorn `--reload`, vite dev server), без пересборки образа.

### Вариант B: локально

#### 1. Python-окружение (parser, converters, backend)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для конвертации `.dwg` дополнительно нужен LibreDWG (`brew install libredwg` / `apt install libredwg-tools`).

#### 2. Backend

```bash
source .venv/bin/activate
cd backend
uvicorn main:app --reload --port 8000
```

Эндпоинты: `POST /api/parse` — принимает `.dxf`, отдаёт `{boundary, restrictions, objects, windows, canopies, meta}`; `POST /api/generate-greenery` — принимает сцену того же формата (+ опциональные `species`/`grid_spacing_m`/`min_tree_spacing_m` через query string), возвращает её же с добавленными деревьями. Пока реализованы только деревья (детерминированный генератор по сетке, без ML); кустарники и газон — TODO. Полное описание алгоритма и параметров — `backend/README.md`.

#### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Открыть `http://localhost:5173`, загрузить DXF (например, из `locations/`), кликнуть по дереву/кусту/лавке/фонарю — выбрать и перетащить.

## Что умеет прототип сейчас

- Парсинг DXF в структурированную сцену: граница участка, здания (с фасадами — окна/козырьки/подъезды), зоны ограничений (коммуникации, дороги, парковки и т.д.), точечные объекты озеленения
- 3D-просмотр сцены с подсветкой зон по степени ограничения (запрещено / предупреждение / разрешено)
- Отступы от здания для посадок разделены по видам (дерево — 5м, кустарник — 1.5м, по СНиП 2.07.01-89*/СП 42.13330.2016) — видны как кольца вокруг здания и проверяются при перетаскивании
- Редактирование расположения деревьев/кустов/лавок/фонарей с проверкой нарушений отступов в реальном времени
- Экспорт отредактированного расположения объектов в JSON
- Конвертация GeoJSON/SHP/DWG в DXF для импорта внешних геоданных (Мосгеотрест, data.mos.ru)
- Кнопка "Сгенерировать растительность автоматически" → `/api/generate-greenery`: расставляет деревья по сетке внутри зон газона, обходя здания/коммуникации/парковки/дорожки с нужным по нормам отступом; не трогает уже расставленные пользователем объекты (`backend/greenery_generator.py`, `backend/setback_norms.py`)
- Запуск в Docker (`docker compose up`)

## Дальше

- Кустарники и газон в автогенераторе (сейчас только деревья — `generate_bushes()`/`generate_lawn()` в `backend/greenery_generator.py`)
- Каталог реальных видов деревьев с разными отступами по породе (интерфейс уже готов — `SPECIES_SETBACK_OVERRIDES` в `backend/setback_norms.py`, каталог пока пуст)
- Параметры генератора (`species`/`grid_spacing_m`/`min_tree_spacing_m`) пока настраиваются только через query string/Swagger — на фронте нет полей для них, плотность посадки нужно подбирать под масштаб конкретной сцены вручную (см. `backend/README.md`, "Плотность посадки")
- Экспорт отредактированного плана обратно в DXF
