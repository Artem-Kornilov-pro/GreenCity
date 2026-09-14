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

# Число процессов uvicorn. Значение по умолчанию (1) сохраняет привычный
# дев-режим с --reload: правки в смонтированном ./backend подхватываются на
# лету. Совместить одно с другим нельзя -- uvicorn не поддерживает --reload
# вместе с --workers, поэтому и развилка.
#
# Зачем вообще: тяжёлые эндпоинты (/api/parse, /api/generate-greenery,
# /api/export-dxf) -- чистая CPU-геометрия на shapely, и потоками она НЕ
# распараллеливается, весь Python-байткод в процессе идёт через один GIL.
# В нагрузочном тесте на 100 одновременных пользователей это выглядело так:
# медиана generate-greenery уехала с 4.6 с до 160 с, а пропускная способность
# всего сервиса упала с ~16 запросов/с почти до нуля -- запросы не отваливались,
# а по очереди делили одно ядро. Настоящий параллелизм здесь даёт только
# несколько ПРОЦЕССОВ: UVICORN_WORKERS=4 (по числу свободных ядер) поднимает
# потолок примерно во столько же раз. Ставить >1 имеет смысл для нагрузочных
# прогонов и продакшена, где --reload всё равно не нужен.
ENV UVICORN_WORKERS=1

CMD if [ "${UVICORN_WORKERS:-1}" -gt 1 ]; then \
      exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers "$UVICORN_WORKERS"; \
    else \
      exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload; \
    fi
