VENV     := $(CURDIR)/.venv
PYTHON   := $(VENV)/bin/python
# Инструменты venv -- через «python -m»: скрипты .venv/bin/pytest, pip,
# uvicorn хранят в первой строке абсолютный путь к интерпретатору и ломаются
# («No such file or directory»), если папку проекта перенесли или
# переименовали после создания venv.
PIP      := $(PYTHON) -m pip
UVICORN  := $(PYTHON) -m uvicorn
RUFF     := $(PYTHON) -m ruff
PYTEST   := $(PYTHON) -m pytest
OUT      ?= output
CATEGORY ?= tree

.PHONY: help venv backend frontend-install frontend frontend-build parse clean \
        lint lint-py lint-web lint-fix models test test-e2e test-cov parser-regression greenplan-regression audit-references corpus-features primers examples docs \
        loadtest loadtest-headless \
        docker-build docker-up docker-down docker-logs docker-prod-up docker-prod-down docker-public-up docker-public-down docker-prune

help:
	@echo "make venv              - создать venv и поставить Python-зависимости"
	@echo "make backend           - запустить FastAPI (http://localhost:8000)"
	@echo "make frontend-install  - npm install во frontend/"
	@echo "make frontend          - запустить dev-сервер фронтенда (http://localhost:5173)"
	@echo "make frontend-build    - собрать production-сборку фронтенда"
	@echo "make parse FILE=path/to.dxf [OUT=output] - разобрать DXF в JSON парсером"
	@echo "make test              - юнит-тесты backend/ и parser/ (pytest, без сети/докера)"
	@echo "make test-e2e          - только сквозные сценарии (tests/e2e: DXF -> GreenPlan -> записка -> DXF)"
	@echo "make test-cov          - то же самое + отчёт о покрытии (terminal + htmlcov/)"
	@echo "make loadtest HOST=... - нагрузочный прогон Locust с веб-интерфейсом (http://localhost:8089)"
	@echo "make loadtest-headless HOST=... - ступенчатый прогон без интерфейса, отчёт в loadtest-report/"
	@echo "make parser-regression [BASE=main] - разбор всех DXF корпуса: текущий код против ревизии BASE"
	@echo "make greenplan-regression [BASE=main] - посадки GreenPlan на тестовых участках и примерах: текущий код против BASE"
	@echo "make audit-references  - аудит эталонов: реальные проекты корпуса против норм отступов (docs/AUDIT_REFERENCES.md)"
	@echo "make corpus-features   - пересчитать data/pattern_corpus_features.json (после правки DXF корпуса)"
	@echo "make primers           - пересобрать синтетические эталоны locations/2x_*_primer и признаки корпуса"
	@echo "make examples          - пересобрать примеры результата examples/ (без LLM) и проверить выходные DXF"
	@echo "make docs              - схемы BPMN (docs/bpmn/) и PDF документации (docs/pdf/; нужны markdown и Chromium)"
	@echo "make lint              - прогнать все линтеры (Python + фронтенд)"
	@echo "make lint-py           - только Python (ruff, конфиг в pyproject.toml)"
	@echo "make lint-web          - только фронтенд (oxlint, конфиг .oxlintrc.json)"
	@echo "make lint-fix          - автоисправление того, что чинится автоматически"
	@echo "make models SRC=path/to/pack [CATEGORY=tree] - конвертировать пак моделей OBJ -> GLB"
	@echo "make clean             - удалить venv, node_modules, кэши сборки"
	@echo "make docker-build      - собрать образы backend+frontend"
	@echo "make docker-up         - поднять оба сервиса через docker compose"
	@echo "make docker-down       - остановить и удалить контейнеры"
	@echo "make docker-logs       - логи обоих сервисов (docker compose up без -d)"
	@echo "make docker-prod-up    - продакшен-режим: статика за nginx на :80, без --reload (docker-compose.prod.yml)"
	@echo "make docker-prod-down  - остановить продакшен-режим"
	@echo "make docker-public-up  - публичный стенд: продакшен-режим + HTTPS (Caddy) для PUBLIC_DOMAIN (docs/PUBLIC_STAND.md)"
	@echo "make docker-public-down - остановить публичный стенд"
	@echo "make docker-prune      - удалить старые образы и кеш сборки старше недели (место на диске сервера)"

# venv пересоздаётся только если список зависимостей новее .venv/bin/activate
$(VENV)/bin/activate: requirements.txt requirements-dev.txt
	python3 -m venv $(VENV)
	$(PIP) install -r requirements-dev.txt
	touch $(VENV)/bin/activate

venv: $(VENV)/bin/activate

backend: $(VENV)/bin/activate
	cd backend && $(UVICORN) main:app --reload --port 8000

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

parse: $(VENV)/bin/activate
	@if [ -z "$(FILE)" ]; then \
		echo "Использование: make parse FILE=path/to.dxf [OUT=output]"; \
		exit 1; \
	fi
	$(PYTHON) parser/parse_dxf.py $(FILE) --out-dir $(OUT)

# Полностью офлайн: MongoDB/Redis подменены mongomock/fakeredis (см.
# backend/tests/conftest.py), LLM не вызывается ни разу (сеть недоступна —
# и не должна быть нужна, реальные вызовы стоят пользователю денег, см.
# backend/tests/test_llm_editor_request.py).
# -n auto -- по числу ядер, не одним процессом (542+ тестов и на слабой
# машине быстрее в разы); для отладки одного теста с pdb/-s это не нужно --
# запускайте $(PYTEST) конкретный_файл.py::тест напрямую, без make.
test: $(VENV)/bin/activate
	$(PYTEST) -n auto

# Только сквозные сценарии (backend/tests/e2e): настоящее приложение через
# HTTP, от загрузки DXF до выгрузки DXF и пояснительной записки.
test-e2e: $(VENV)/bin/activate
	$(PYTEST) -n auto -m e2e

# Деградация разбора: каждый DXF корпуса -- кодом из BASE и текущим, сцены
# сравниваются (tools/parser_regression.py).
parser-regression: $(VENV)/bin/activate
	$(PYTHON) tools/parser_regression.py --base $(or $(BASE),main)

greenplan-regression: $(VENV)/bin/activate
	$(PYTHON) tools/greenplan_regression.py --base $(or $(BASE),main)

audit-references: $(VENV)/bin/activate
	$(PYTHON) tools/audit_references.py --write

corpus-features: $(VENV)/bin/activate
	cd backend && $(PYTHON) -m greenplan.pattern_corpus

primers: $(VENV)/bin/activate
	$(PYTHON) tools/primers/build_primers.py
	$(MAKE) corpus-features

examples: $(VENV)/bin/activate
	$(PYTHON) tools/build_examples.py
	for s in 07_nizhnie_polya 06_kamchatskaya_ulitsa 12_natashinsky_proezd; do \
		$(PYTHON) tools/check_export.py locations/$$s/$$s.dxf examples/$$s/$${s}_greenplan.dxf examples/$$s/$${s}_explanations.json || exit 1; \
	done

docs:
	python3 tools/docs/build_bpmn.py
	python3 tools/docs/build_pdf.py

test-cov: $(VENV)/bin/activate
	$(PYTEST) -n auto --cov --cov-report=term-missing --cov-report=html

# Нагрузочный прогон (tools/loadtest/locustfile.py, описание -- tools/loadtest/README.md).
# HOST -- адрес стенда; ступени -- LOAD_STEP_USERS, LOAD_STEP_SECONDS,
# LOAD_MAX_USERS; LOAD_THINK=0 -- без пауз между запросами; USERS_CLASSES --
# какие пользователи (Visitor ApiReader Engineer), по умолчанию все.
HOST ?= http://localhost
USERS_CLASSES ?=
loadtest-deps: $(VENV)/bin/activate
	$(PIP) install -q -r tools/loadtest/requirements.txt

loadtest: loadtest-deps
	$(PYTHON) -m locust -f tools/loadtest/locustfile.py --host $(HOST) $(USERS_CLASSES)

loadtest-headless: loadtest-deps
	mkdir -p loadtest-report
	LOAD_SHAPE=step $(PYTHON) -m locust -f tools/loadtest/locustfile.py --host $(HOST) --headless --processes 4 \
		--csv loadtest-report/run --csv-full-history --html loadtest-report/report.html $(USERS_CLASSES)
	$(PYTHON) tools/loadtest/steps.py loadtest-report/run_stats_history.csv

# Оба линтера завершаются ненулевым кодом при любой находке (у oxlint для
# этого нужен --deny-warnings) — цель годится как гейт в CI, а не только
# для чтения глазами.
lint: lint-py lint-web

lint-py: $(VENV)/bin/activate
	$(RUFF) check .

lint-web:
	cd frontend && npx oxlint --deny-warnings

lint-fix: $(VENV)/bin/activate
	$(RUFF) check . --fix
	cd frontend && npx oxlint --fix

# Конвертация готового пака 3D-моделей в .glb для фронтенда.
# Подробности и требования -- frontend/public/models/README.md.
models:
	@if [ -z "$(SRC)" ]; then \
		echo "Использование: make models SRC=path/to/pack [CATEGORY=tree]"; \
		exit 1; \
	fi
	node tools/convert_models.mjs $(SRC) --category $(CATEGORY)

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose up --build

PROD_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.prod.yml

docker-prod-up:
	$(PROD_COMPOSE) up -d --build

docker-prod-down:
	$(PROD_COMPOSE) down

PUBLIC_COMPOSE := $(PROD_COMPOSE) -f docker-compose.public.yml

docker-public-up:
	$(PUBLIC_COMPOSE) up -d --build

# Для остановки домен не нужен -- заглушка, если его нет в окружении и .env.
docker-public-down:
	PUBLIC_DOMAIN=$${PUBLIC_DOMAIN:-stopping} $(PUBLIC_COMPOSE) down

# После каждой пересборки старые образы и слои кеша сборки остаются на диске
# (каждая сборка фронтенда и бэкенда -- сотни МБ). Тома с данными (mongo,
# prometheus, loki, grafana) не трогаются.
docker-prune:
	docker image prune -f
	docker builder prune -f --filter until=168h
