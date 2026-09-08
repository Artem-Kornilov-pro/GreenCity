VENV     := $(CURDIR)/.venv
PYTHON   := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
UVICORN  := $(VENV)/bin/uvicorn
OUT      ?= output

.PHONY: help venv backend frontend-install frontend frontend-build parse clean \
        docker-build docker-up docker-down docker-logs

help:
	@echo "make venv              - создать venv и поставить Python-зависимости"
	@echo "make backend           - запустить FastAPI (http://localhost:8000)"
	@echo "make frontend-install  - npm install во frontend/"
	@echo "make frontend          - запустить dev-сервер фронтенда (http://localhost:5173)"
	@echo "make frontend-build    - собрать production-сборку фронтенда"
	@echo "make parse FILE=path/to.dxf [OUT=output] - разобрать DXF в JSON парсером"
	@echo "make clean             - удалить venv, node_modules, кэши сборки"
	@echo "make docker-build      - собрать образы backend+frontend"
	@echo "make docker-up         - поднять оба сервиса через docker compose"
	@echo "make docker-down       - остановить и удалить контейнеры"
	@echo "make docker-logs       - логи обоих сервисов (docker compose up без -d)"

# venv пересоздаётся только если requirements.txt новее .venv/bin/activate
$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt
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
