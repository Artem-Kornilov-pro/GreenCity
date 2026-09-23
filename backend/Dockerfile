# --- Стадия сборки LibreDWG --------------------------------------------------
# dwg2dxf нужен /api/parse-dwg (backend/exchange/dwg_batch_converter.py, issue #50) для
# конвертации .dwg в .dxf. LibreDWG НЕ запакетирован ни в Debian (trixie,
# текущая база python:3.12-slim), ни в Ubuntu (noble, universe включён) --
# `apt install libredwg-tools` из более старых заметок по проекту на практике
# не находит пакет (проверено на обеих базах). Поэтому собираем из офиц.
# релизного тарбола (в нём уже сгенерирован ./configure, autoreconf не нужен).
# --disable-shared -- статически линкует саму библиотеку внутрь бинарника,
# наружу торчат только системные libc/libm (см. ldd), поэтому в финальный
# образ переносится один файл, без .so. Сборка src/ занимает ~2 минуты --
# отдельная стадия и Docker layer cache дают то, что она не повторяется на
# каждую пересборку, пока ARG LIBREDWG_VERSION не меняется.
FROM python:3.12-slim AS libredwg-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*
ARG LIBREDWG_VERSION=0.14
RUN curl -sL -o /tmp/libredwg.tar.gz \
        "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.gz" \
    && tar xzf /tmp/libredwg.tar.gz -C /tmp \
    && cd "/tmp/libredwg-${LIBREDWG_VERSION}" \
    && ./configure --disable-bindings --disable-shared \
    && make -j"$(nproc)" -C src \
    && make -j"$(nproc)" -C programs dwg2dxf \
    && strip programs/dwg2dxf \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf

# Собирать из корня репозитория (build context = .), не из backend/ --
# main.py импортирует parser/parse_dxf.py по относительному пути
# (Path(__file__).parent.parent / "parser"), поэтому оба каталога должны
# сохранить то же взаимное расположение, что и в репозитории.
FROM python:3.12-slim

WORKDIR /app

COPY --from=libredwg-builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY parser/ ./parser/
COPY backend/ ./backend/
# GreenPlan retrieval-корпус (backend/greenplan/pattern_corpus.py читает
# locations/<slug>/<slug>.dxf и data/pattern_corpus.yaml по относительному от
# backend/ пути) -- в dev-режиме (docker-compose, --reload) эти же два
# каталога приходят volume-мountом поверх этой COPY, здесь она нужна для
# продакшен-сборки (UVICORN_WORKERS>1, без volume'ов вовсе).
COPY locations/ ./locations/
COPY data/ ./data/

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
