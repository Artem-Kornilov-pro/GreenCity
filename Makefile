VENV     := $(CURDIR)/.venv
PYTHON   := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
UVICORN  := $(VENV)/bin/uvicorn
RUFF     := $(VENV)/bin/ruff
PYTEST   := $(VENV)/bin/pytest
OUT      ?= output
CATEGORY ?= tree

.PHONY: help venv backend frontend-install frontend frontend-build parse clean \
        lint lint-py lint-web lint-fix models test test-cov \
        docker-build docker-up docker-down docker-logs

help:
	@echo "make venv              - создать venv и поставить Python-зависимости"
	@echo "make backend           - запустить FastAPI (http://localhost:8000)"
	@echo "make frontend-install  - npm install во frontend/"
	@echo "make frontend          - запустить dev-сервер фронтенда (http://localhost:5173)"
	@echo "make frontend-build    - собрать production-сборку фронтенда"
	@echo "make parse FILE=path/to.dxf [OUT=output] - разобрать DXF в JSON парсером"
	@echo "make test              - юнит-тесты backend/ и parser/ (pytest, без сети/докера)"
	@echo "make test-cov          - то же самое + отчёт о покрытии (terminal + htmlcov/)"
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
test: $(VENV)/bin/activate
	$(PYTEST)

test-cov: $(VENV)/bin/activate
	$(PYTEST) --cov --cov-report=term-missing --cov-report=html

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
