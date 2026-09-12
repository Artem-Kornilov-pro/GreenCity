"""
Метрики Prometheus для дашбордов Grafana.

Стандартные HTTP-метрики (запросы/с, латентность по хендлеру и коду ответа,
запросы в процессе обработки) даёт `prometheus-fastapi-instrumentator` --
подключается один раз в main.py (`Instrumentator().instrument(app).expose(app)`)
и не требует ручной разметки каждого эндпоинта.

Здесь -- только БИЗНЕС-метрики, которые generic HTTP-инструментация в принципе
не может знать: сколько DXF реально распарсено, сколько объектов озеленения
сгенерировано, как часто LLM отвечает успехом/ошибкой/недоступностью, сколько
попыток входа/регистрации отбито лимитом частоты и т.п. Эндпоинты (main.py)
вызывают функции ниже в дополнение к своей обычной логике -- сбой сбора
метрики никогда не должен уронить сам запрос, поэтому все функции здесь --
простые синхронные inc()/observe() без I/O и без исключений наружу.
"""

from __future__ import annotations

from prometheus_client import Counter

dxf_parses_total = Counter(
    "greencity_dxf_parses_total",
    "Успешно разобранные DXF-файлы (/api/parse)",
)

dxf_parse_errors_total = Counter(
    "greencity_dxf_parse_errors_total",
    "Файлы, которые не удалось разобрать как DXF (/api/parse)",
)

dxf_exports_total = Counter(
    "greencity_dxf_exports_total",
    "Экспорт сцены в DXF (/api/export-dxf)",
)

greenery_generated_total = Counter(
    "greencity_greenery_generated_total",
    "Сгенерированные объекты озеленения (/api/generate-greenery), по типу",
    ["object_type"],
)

llm_edit_requests_total = Counter(
    "greencity_llm_edit_requests_total",
    "Запросы правки плана текстом (/api/edit-with-text), по исходу",
    ["outcome"],  # success | llm_error | not_configured
)

auth_registrations_total = Counter(
    "greencity_auth_registrations_total",
    "Регистрации пользователей, по исходу",
    ["outcome"],  # success | rejected | rate_limited
)

auth_logins_total = Counter(
    "greencity_auth_logins_total",
    "Попытки входа, по исходу",
    ["outcome"],  # success | rejected | rate_limited
)

rate_limit_blocks_total = Counter(
    "greencity_rate_limit_blocks_total",
    "Запросы, отклонённые ограничением частоты (cache.check_rate_limit), по эндпоинту",
    ["endpoint"],
)
