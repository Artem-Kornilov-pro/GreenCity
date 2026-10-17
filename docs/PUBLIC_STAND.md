# Публичный стенд для экспертов

Эксперт открывает GreenCity по ссылке, без установки Docker. Стенд — тот же продакшен-режим
(`docker-compose.prod.yml`), плюс Caddy с автоматическим HTTPS (`docker-compose.public.yml`,
`deploy/caddy/Caddyfile`).

## Что нужно

| | |
|---|---|
| Сервер | Linux x86-64 (Ubuntu 22.04+ или МосТех.ОС), 4 vCPU, 8 ГБ ОЗУ, 40 ГБ SSD — как для `make docker-prod-up` |
| Docker | Docker Engine и Compose **2.24.4 или новее** (теги `!override`/`!reset`) |
| Домен | DNS-запись **A** домена (например, `greencity.example.ru`) на публичный адрес сервера |
| Порты | 80 и 443 открыты наружу (80 — для выпуска сертификата Let's Encrypt) |

## Запуск

```bash
git clone <репозиторий> greencity && cd greencity
cp .env.example .env
```

В `.env` задайте:

```dotenv
PUBLIC_DOMAIN=greencity.example.ru      # домен стенда
JWT_SECRET=<длинная случайная строка>   # openssl rand -hex 32
GRAFANA_ADMIN_PASSWORD=<пароль>
# по желанию -- ассистент на LLM:
# YANDEX_CLOUD_API_KEY=...  YANDEX_CLOUD_FOLDER=...
```

```bash
make docker-public-up
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.public.yml ps
```

Через 1–2 минуты после старта Caddy получит сертификат, и сайт откроется по
`https://greencity.example.ru`. Остановить — `make docker-public-down`.

## Что открыто наружу

- Только Caddy (80 → редирект на 443, 443). Фронтенд, бэкенд, MongoDB, Redis, Prometheus и Loki — во
  внутренней сети Docker.
- Swagger и схема OpenAPI выключены (продакшен-режим, `API_DOCS=0`).
- Grafana — только на `127.0.0.1` сервера: `ssh -L 3001:127.0.0.1:3001 <сервер>`, затем
  `http://localhost:3001`.
- Защита тяжёлых запросов работает как в продакшене: очередь расчётов, лимиты частоты по адресу
  (429), лимит размера тела (413), не больше `DWG_MAX_PARALLEL` разборов комплектов одновременно.

## Проверка стенда

1. `https://<домен>` открывается, сертификат действителен, `http://` переводит на `https://`.
2. «Попробовать без регистрации» → «Загрузить чертёж» → `locations/location_test/01_school/01_school.dxf`
   → GreenPlan → «Экспорт DXF»: полоса над сценой — «Проверка выгрузки пройдена».
3. Загрузка комплекта: zip с несколькими DWG/DXF объекта.
4. `https://<домен>/api/docs` и `/api/openapi.json` — 404 (Swagger закрыт).

## Проверка без домена

На своей машине: `PUBLIC_DOMAIN=localhost PUBLIC_HTTP_PORT=8080 PUBLIC_HTTPS_PORT=8443 make docker-public-up`
— Caddy выпустит самоподписанный сертификат, сайт — `https://localhost:8443` (браузер предупредит
о сертификате).

## Обновление

```bash
git pull && make docker-public-up   # пересобирает образы; тома с данными сохраняются
make docker-prune                   # раз в неделю -- старые образы и кеш сборки
```
