# Импорт пака моделей — 19 сентября 2026

Фиксирую, что сейчас лежит в `frontend/public/models/` и как это работает, чтобы
не путать с отдельной, не связанной с этим пайплайном работой над каталогом
видов растений в `Датасет/Модельки для генерации/`.

## Что подключено

По `manifest.json` и `backend/catalog_generated.json` — **46 моделей**, оба
файла и сами `.glb` уже лежат на диске (проверено: 51 файл в этой папке, 1.7 МБ
суммарно, включая общие текстуры `tree_color.jpg`, `wood-texture.jpg` +
`wood-texture-normalMap.jpg`):

- **31 дерево** — файлы вида `100_mesh_099.glb`, `101_mesh_100.glb` и т.д.
  (имя — это `id` из исходного `.obj`, автоматически приведённый к
  латинице/цифрам скриптом `toId()`, см. `tools/convert_models.mjs`).
- **15 МАФ** — 10 уже были раньше (`bench_long/medium/short`, `chair_folding`,
  `chair_hex`, `lounger_narrow/wide`, `picnic_table_bench`, `stool`, `table`),
  ещё 5 новых: `bench_a`…`bench_e`.

### Как классифицированы деревья

`tools/convert_models.mjs` не знает пород — он меряет bounding box каждого
`.obj` (высота по Y, радиус кроны по X/Z) и выводит класс из чисел:

| size_class | height | crown_class | height / width кроны |
|---|---|---|---|
| low | < 3 м | spreading | < 1.3 |
| medium | 3–6 м | regular | 1.3–2.5 |
| tall | > 6 м | columnar | ≥ 2.5 |

Фактическое распределение по 31 дереву в `catalog_generated.json`:

- по высоте: **medium — 23, tall — 7, low — 1** (диапазон высот 2.76–7.72 м);
- по кроне: **regular — 21, spreading — 9, columnar — 1**.

Подпись вида `"Дерево 100 — среднее, раскидистое, 4.33 м"` собирается из этих
же чисел (`toLabel()`) — никаких названий пород в этом пайплайне нет и не
может быть, пак их не содержал.

## Как это доезжает до сцены

1. `tools/convert_models.mjs SRC=<пак> [--category tree|bush|furniture]`
   конвертирует `.obj → .glb` (`obj2gltf --binary --separateTextures`),
   **дополняя** (не перезаписывая) `manifest.json` и `catalog_generated.json`
   по `id` — это позволяло гонять деревья и МАФ отдельными запусками.
2. `backend/core/plant_catalog.py::load_catalog()` отдаёт `[*CATALOG, *_load_generated()]`
   — статических 19 записей (заглушки без реальных `.glb`, они и раньше не
   имели файлов моделей) плюс эти 46 сгенерированных. Бэкенд перечитывает
   `catalog_generated.json` на каждый запрос `/api/catalog` — правки видно
   сразу, без рестарта.
3. Фронтенд (`frontend/src/catalog.ts::fetchModelManifest`) читает
   `manifest.json`, чтобы знать, для каких `id` реально есть `.glb`.
4. `ObjectVisual.tsx`: если модель есть в манифесте — рисуется `.glb`
   (`useGLTF` + клон сцены), если нет — примитив по `render.shape` из каталога.
   Так дерево/куст без модели никогда не "падает", а просто выглядит проще —
   штатный режим, а не баг.
5. `resolveCatalogItem()` в `catalog.ts` раздаёт деревьям из DXF-подосновы и от
   автогенератора **разные** модели из доступных (детерминированный хэш по
   `id` объекта) — иначе двор из сотен деревьев состоял бы из одного клона.

## Важно: это НЕ то же самое, что каталог видов в «Модельки для генерации»

Отдельная работа в `Датасет/Модельки для генерации/` (файлы
`greencity_species_catalog.json`, `greencity_archetype_model_mapping.json`) —
это 232 конкретных вида растений (Клён остролистный, Ель колючая и т.д.),
разложенных по 31 именованному архетипу (T1 `tree_round`, C1 `conifer_spruce`
и т.д.), с моделями из паков Quaternius/Kenney разного присхождения.

Эти два трека сейчас **не связаны**:

- Пайплайн `convert_models.mjs` / `catalog_generated.json`, описанный выше, не
  читает и не знает про `greencity_species_catalog.json` — деревья в нём
  безымянные ("Дерево 100"), классифицируются только по геометрии.
- Чтобы конкретные виды с русскими названиями и наши архетипы (ель, плакучая
  ива и т.д.) появились в этом приложении под своими именами — нужен
  отдельный шаг интеграции (например, свой скрипт по образцу
  `convert_models.mjs`, который вместо generic `toLabel()` брал бы название
  вида и `model_group` из `greencity_species_catalog.json`), это ещё не
  сделано.
- В частности, найденная (но по вашей просьбе **не вставленная** никуда)
  модель плакучей ивы под архетип T5 `tree_weeping` — сейчас нигде в файлах
  проекта не лежит, есть только в переписке со ссылками на Poly Pizza/bunpav.

## Проверенные файлы (для трассируемости)

`frontend/public/models/{manifest.json,README.md}`,
`backend/{catalog_generated.json,plant_catalog.py}`,
`frontend/src/{catalog.ts,scene/ObjectVisual.tsx}`, `tools/convert_models.mjs`,
`README.md` (корень репозитория) — плюс список файлов в
`frontend/public/models/` на диске.
