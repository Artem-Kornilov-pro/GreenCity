# Собирать из корня репозитория (build context = .), не из backend/ --
# main.py импортирует parser/parse_dxf.py по относительному пути
# (Path(__file__).parent.parent / "parser"), поэтому оба каталога должны
# сохранить то же взаимное расположение, что и в репозитории.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY parser/ ./parser/
COPY backend/ ./backend/

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
