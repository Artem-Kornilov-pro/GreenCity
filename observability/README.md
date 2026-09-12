# Наблюдаемость: метрики (Prometheus) + логи (Loki) + дашборды (Grafana)

Полностью необязательная часть стека -- сам редактор (парсинг DXF, генерация
озеленения, правка текстом, аккаунты) не знает об этих четырёх сервисах и
продолжает работать, даже если они не подняты. `backend` в любом случае
отдаёт `GET /metrics` и пишет структурированные JSON-логи в stdout -- это не
требует наличия Prometheus/Loki, просто некому будет их забрать и показать.

## Из чего состоит

| Сервис | Порт (хост) | Что делает |
|---|---|---|
| `prometheus` | `9090` | Каждые 15с забирает `GET http://backend:8000/metrics` (`observability/prometheus/prometheus.yml`) |
| `loki` | `3100` | Хранилище логов (файловая система, без S3/кластера -- см. `observability/loki/loki-config.yml`) |
| `promtail` | -- | Читает логи ВСЕХ контейнеров compose через Docker API (сокет докера, только для чтения) и отправляет в `loki` (`observability/promtail/promtail-config.yml`) |
| `grafana` | `3001` | UI поверх Prometheus и Loki, дашборды подключаются автоматически при старте |

Все четыре добавлены в `docker-compose.yml` в корне репозитория и
поднимаются вместе со всем остальным одной и той же командой
`docker compose up -d --build` -- по требованию проекта весь стек собирается
и запускается только через docker compose и целиком, без отдельных
профилей/флагов.

Каждый из четырёх сервисов зависит от предыдущего по готовности
(`depends_on: condition: service_healthy`), а не просто по факту запуска --
grafana не стартует раньше, чем prometheus и loki реально ответят на свой
healthcheck, promtail -- раньше loki. У всех `restart: unless-stopped`:
контейнер, упавший сам по себе, Docker поднимает заново автоматически (но не
трогает то, что вы остановили сами).

## Grafana

`http://localhost:3001`, логин/пароль -- `admin`/`admin` (задаётся
`GF_SECURITY_ADMIN_PASSWORD` в `docker-compose.yml`; для любого запуска не
только на своей машине -- поменять). При первом старте через провижининг
(`observability/grafana/provisioning/`) сразу доступны:

* источники данных **Prometheus** и **Loki** (`datasources/datasources.yml`)
  -- добавлять руками через UI не нужно;
* папка дашбордов **GreenCity** (`dashboards/dashboards.yml` + сами дашборды
  из `observability/grafana/dashboards/*.json`):
  * **GreenCity — обзор** (`greencity-overview.json`) -- HTTP-метрики
    (запросы/с по эндпоинту, ошибки 5xx, латентность p50/p95/p99) и
    бизнес-метрики (парсинг/экспорт DXF, сгенерированное озеленение по типу,
    исходы правки текстом через LLM, регистрации/входы/блокировки лимитом
    частоты);
  * **GreenCity — логи** (`greencity-logs.json`) -- объём логов по уровню,
    отдельные панели для предупреждений/ошибок, событий аккаунтов
    (`greencity.auth`) и журнала доступа (`greencity.access`).

Прямые ссылки (после первого входа admin/admin открываются сразу, без
навигации по меню):
- Обзор: http://localhost:3001/d/greencity-overview
- Логи: http://localhost:3001/d/greencity-logs

Оба дашборда лежат в файлах и подхватываются автоматически -- изменения,
сделанные в самом UI Grafana (`allowUiUpdates: true`), в файлы не
сохраняются и потеряются при пересоздании контейнера; менять сами графики
на постоянной основе -- через правку JSON в этой папке.

## Метрики (`backend/metrics.py`)

Стандартные HTTP-метрики (запросы/с, латентность, размер запроса/ответа) даёт
`prometheus-fastapi-instrumentator` без единой строки разметки в каждом
эндпоинте (`Instrumentator().instrument(app).expose(app)` в `main.py`).

Бизнес-метрики -- то, что HTTP-инструментация в принципе не может знать,
увеличиваются явно в нужных местах `main.py`:

| Метрика | Тип | Метки | Когда растёт |
|---|---|---|---|
| `greencity_dxf_parses_total` | Counter | -- | Успешный `/api/parse` |
| `greencity_dxf_parse_errors_total` | Counter | -- | `/api/parse` не смог разобрать файл |
| `greencity_dxf_exports_total` | Counter | -- | `/api/export-dxf` |
| `greencity_greenery_generated_total` | Counter | `object_type` (tree/bush/lawn_patch) | `/api/generate-greenery`, на каждый добавленный объект |
| `greencity_llm_edit_requests_total` | Counter | `outcome` (success/llm_error/not_configured) | `/api/edit-with-text` |
| `greencity_auth_registrations_total` | Counter | `outcome` (success/rejected/rate_limited) | `/api/auth/register` |
| `greencity_auth_logins_total` | Counter | `outcome` (success/rejected/rate_limited) | `/api/auth/login` |
| `greencity_rate_limit_blocks_total` | Counter | `endpoint` (register/login) | Запрос отклонён `cache.check_rate_limit` |

## Логи (`backend/logging_config.py`)

`configure_logging()` (вызывается один раз при импорте `main.py`) заменяет
собой прежний `logging.basicConfig` на JSON-форматтер: одна запись -- одна
строка `{"ts", "level", "logger", "message", "request_id", ...}` в stdout.
Уровень -- через `LOG_LEVEL` (по умолчанию `INFO`; `DEBUG` при отладке,
`WARNING` в проде, задаётся в `.env`/`docker-compose.yml`).

`RequestLoggingMiddleware` -- отдельная строка на каждый HTTP-запрос
(`greencity.access`: метод, путь, код ответа, длительность, IP клиента) плюс
`request_id`, который тем же значением попадает во ВСЕ остальные логи,
написанные во время обработки этого запроса (`_RequestIdFilter`, ContextVar)
-- по нему можно собрать все строки одного запроса, не имея готовой системы
трассировки.

Логгер `uvicorn.access`/`uvicorn.error` эта конфигурация не трогает
(`propagate=False`) -- его собственный вывод остаётся как есть.

Логгеры приложения (появляются в Grafana Explore по `logger=`):

| Логгер | О чём |
|---|---|
| `greencity.access` | Каждый HTTP-запрос (RequestLoggingMiddleware) |
| `greencity.auth` | Регистрации/входы/выходы, отклонённые попытки, блокировки лимитом частоты -- без паролей, только имя пользователя и IP |
| `greencity.parse` | Каждый `/api/parse` -- успех (кол-во объектов/зон) или причина отказа |
| `greencity.generate` | Каждый `/api/generate-greenery` -- сколько объектов добавлено |
| `greencity.export` | Каждый `/api/export-dxf` |
| `greencity.llm` | Запросы к LLM (`llm_editor.py`, было и раньше) -- инструкция, применённые/отклонённые операции |
| `greencity.db` / `greencity.cache` | Недоступность MongoDB/Redis (было и раньше) |

## Проверить, что всё работает

```bash
curl -s http://localhost:8000/metrics | grep greencity_   # бизнес-метрики backend
curl -s http://localhost:9090/api/v1/targets              # backend должен быть "up" в Prometheus
curl -s http://localhost:3100/ready                        # Loki
```

В Grafana (`http://localhost:3001`) дашборды -- в папке **GreenCity** в
левом меню Dashboards; живые логи -- также через Explore, источник **Loki**,
запрос `{compose_service="backend"}`.
