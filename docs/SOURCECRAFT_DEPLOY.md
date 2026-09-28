# Деплой backend в Yandex Cloud через SourceCraft CI/CD

`.sourcecraft/ci.yaml` (`deploy-backend-workflow`) собирает `backend/Dockerfile`
и разворачивает его в Yandex Serverless Containers на каждый push в `main`.
Это НЕ настраивается автоматически — SourceCraft не имеет доступа к вашему
облаку, пока вы явно не создадите связку "сервисный аккаунт → Service
Connection → реестр". Без шагов ниже джоба `deploy-backend-task` упадёт на
первом же кубике (`get-iam-token`).

Основано на официальном примере SourceCraft
([sourcecraft/yc-ci-cd-serverless](https://sourcecraft.dev/sourcecraft/yc-ci-cd-serverless)) —
если что-то в интерфейсе Yandex Cloud/SourceCraft изменится, сверяйтесь с
ним, а не только с этим файлом.

## Что деплоится, а что нет

Разворачивается **только backend** (`backend/Dockerfile`, уже готов к проду —
образ по умолчанию запускается без `--reload`, `UVICORN_WORKERS` процессов;
дев-режим включает только `UVICORN_RELOAD=1`). Без:

- **MongoDB/Redis** — `/api/auth/*` и `/api/projects/*` ответят понятной
  503-ошибкой (см. `backend/storage/db.py`/`backend/storage/cache.py`), а сам редактор
  (парсинг DXF, генерация, правка текстом, GreenPlan) от них не зависит и
  работает как обычно — это штатное поведение, не поломка деплоя.
- **Yandex AI Studio** (без `YANDEX_CLOUD_API_KEY`/`YANDEX_CLOUD_FOLDER`) — правка текстом ответит 503, `POST /api/greenplan/report` ответит
  `report_error` вместо текста, расстановка (`/api/greenplan/generate`) не пострадает.
- **Frontend** — продакшен-образ `frontend/Dockerfile.prod` (статика за nginx,
  `/api/` проксируется на адрес из `BACKEND_URL`) собирается в CI и
  публикуется в ghcr.io (`docker-publish.yml`), но в этот workflow SourceCraft
  не входит. Проще всего поднять весь стек на одной ВМ:
  `make docker-prod-up` (см. README, «Docker на сервере»).

**Бэкенд в Serverless Containers опубликован напрямую**, без прокси
фронтенда, в отличие от docker compose, где он доступен только из сети
контейнеров. Для такого развёртывания передайте через `--environment`:

- `API_DOCS=0` — выключить Swagger и схему OpenAPI;
- `FORWARDED_ALLOW_IPS=127.0.0.1` (или адрес своего прокси) — иначе клиент
  подделает `X-Forwarded-For` и обойдёт лимиты частоты;
- `CORS_ALLOW_ORIGINS=https://<адрес фронтенда>` — если фронтенд живёт на
  другом домене: по умолчанию CORS выключен, и браузер с чужого адреса
  к API не обратится;
- `REDIS_URL` — без Redis лимиты частоты тяжёлых эндпоинтов не действуют.

Для полноценного демо с аккаунтами/LLM нужно отдельно поднять MongoDB/Redis
(например тоже в Yandex Cloud) и передать `MONGO_URI`/`REDIS_URL`/
`YANDEX_CLOUD_API_KEY`/`YANDEX_CLOUD_FOLDER` через `--environment` в шаге `deploy` — сейчас там только
`UVICORN_WORKERS`.

## 1. Сервисный аккаунт в Yandex Cloud

1. [Создайте сервисный аккаунт](https://yandex.cloud/ru/docs/iam/operations/sa/create) —
   от его имени CI/CD будет собирать образ и разворачивать контейнер.
2. [Назначьте ему на каталог](https://yandex.cloud/ru/docs/iam/operations/sa/assign-role-for-sa)
   три роли:
   - `serverless-containers.editor`
   - `container-registry.images.pusher`
   - `iam.serviceAccounts.user`

## 2. Container Registry

[Создайте реестр](https://yandex.cloud/ru/docs/container-registry/operations/registry/registry-create)
и [посмотрите его идентификатор](https://yandex.cloud/ru/docs/container-registry/operations/registry/registry-list) —
он понадобится в шаге 4.

## 3. Service Connection в SourceCraft

В настройках организации/репозитория SourceCraft → **Service connections** →
**New service connection**: укажите имя `default-service-connection` (ровно
это имя уже прописано в `.sourcecraft/ci.yaml`, `tokens.SERVICE_CONNECTION`)
и сервисный аккаунт из шага 1. Подробнее —
[документация SourceCraft](https://sourcecraft.dev/portal/docs/ru/sourcecraft/operations/service-connections).

## 4. Прописать идентификатор реестра

В `.sourcecraft/ci.yaml`, задача `deploy-backend-task`, замените
`<YOUR_DOCKER_REGISTRY_ID>` на идентификатор реестра из шага 2:

```yaml
env:
  YC_DOCKER_REGISTRY_URI: cr.yandex/<YOUR_DOCKER_REGISTRY_ID>  # <- сюда
```

## 5. Проверить

Запушьте любое изменение в `main` (или сам этот файл) — в SourceCraft
откроется вкладка CI/CD с ходом выполнения `deploy-backend-workflow`. После
успеха адрес контейнера покажет `yc serverless container get --name
greencity-backend`.

## Удалить, если больше не нужно

Чтобы не платить за неиспользуемую инфраструктуру, удалите по порядку:
[Docker-образ в реестре](https://yandex.cloud/ru/docs/container-registry/operations/docker-image/docker-image-delete),
[сам реестр](https://yandex.cloud/ru/docs/container-registry/operations/registry/registry-delete),
[Serverless-контейнер](https://yandex.cloud/ru/docs/serverless-containers/operations/delete).
