#!/usr/bin/env node
/**
 * Пакетная конвертация 3D-моделей из готовых паков в .glb для веба.
 *
 * Зачем: скачанные паки (cgtrader и подобные) идут в OBJ/FBX/BLEND, а грузить
 * их в браузер плохо -- нет бинарной упаковки, текстуры отдельными файлами, на
 * сотнях моделей это заметно тормозит. .glb -- один бинарник на модель.
 *
 * Что делает:
 *   1. конвертирует все .obj из папки-источника в .glb (через obj2gltf);
 *   2. кладёт результат в frontend/public/models/;
 *   3. пишет manifest.json -- по нему фронтенд понимает, какие модели реально
 *      есть (чего нет -- рисуется примитив-заглушка);
 *   4. пишет backend/catalog_generated.json -- записи каталога для
 *      сконвертированных моделей, чтобы 200 деревьев из пака появились в
 *      панели "Добавить объект" без ручной правки plant_catalog.py.
 *
 * Использование:
 *   node tools/convert_models.mjs <папка-с-моделями> [--category tree]
 *
 * FBX не конвертируется этим скриптом: для него нужен отдельный бинарь
 * FBX2glTF. Если в паке есть и OBJ, и FBX -- берите OBJ, он проще и его
 * достаточно.
 */

import { execFile } from "node:child_process";
import { mkdir, readdir, readFile, writeFile, stat } from "node:fs/promises";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MODELS_DIR = join(ROOT, "frontend", "public", "models");
const MANIFEST_PATH = join(MODELS_DIR, "manifest.json");
const GENERATED_CATALOG_PATH = join(ROOT, "backend", "catalog_generated.json");

// Габариты по умолчанию для сгенерированных записей каталога. Реальные размеры
// из модели тут не вычисляются: для нормативных отступов важен вид посадки
// (дерево/кустарник), а не точная высота конкретной модели, а для превью
// достаточно порядка величины.
const CATEGORY_DEFAULTS = {
  tree: { height: 3.0, radius: 0.9, trunk_height: 1.2, setback_kind: "tree", object_type: "tree", shape: "cluster", color: "#3a8f45" },
  bush: { height: 1.0, radius: 0.5, trunk_height: null, setback_kind: "bush", object_type: "bush", shape: "sphere", color: "#4a9450" },
  furniture: { height: 1.0, radius: 0.4, trunk_height: null, setback_kind: null, object_type: "bench", shape: "box", color: "#7a5230" },
};

// Габариты берём из самой геометрии: у каждой модели пака своя высота, и
// подставлять всем одно дефолтное число -- значит рисовать в сцене деревья
// заведомо не того размера и считать по ним отступы наугад.
async function measureObj(objPath) {
  const text = await readFile(objPath, "utf8");
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  let minZ = Infinity, maxZ = -Infinity;
  for (const line of text.split("\n")) {
    if (!line.startsWith("v ")) continue;
    const [x, y, z] = line.split(/\s+/).slice(1, 4).map(Number);
    if (Number.isNaN(x) || Number.isNaN(y) || Number.isNaN(z)) continue;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  if (!Number.isFinite(minY)) return null;
  return {
    height: Number((maxY - minY).toFixed(2)),
    radius: Number((Math.max(maxX - minX, maxZ - minZ) / 2).toFixed(2)),
  };
}

// Классы формы выводим из измеренной геометрии: пород пак не указывает, а
// "Дерево 137" не говорит ни человеку, ни агенту ничего. Высота и отношение
// высоты к ширине кроны -- то, что реально можно померить и по чему реально
// выбирают: низкое раскидистое под окна, колонновидное вдоль дорожки.
function classify(measured) {
  if (!measured) return { size_class: null, crown_class: null };
  const width = measured.radius * 2;
  const ratio = width > 0 ? measured.height / width : 0;
  const size_class = measured.height < 3 ? "low" : measured.height <= 6 ? "medium" : "tall";
  const crown_class = ratio >= 2.5 ? "columnar" : ratio >= 1.3 ? "regular" : "spreading";
  return { size_class, crown_class };
}

const SIZE_WORD = { low: "низкое", medium: "среднее", tall: "высокое" };
const CROWN_WORD = { columnar: "колонновидное", regular: "обычная крона", spreading: "раскидистое" };

// Человекочитаемая подпись для списка: в паках имена вида "01_Mesh.001"
// бесполезны, а выбирать из 200 позиций удобнее по номеру и высоте.
function toLabel(id, category, measured, classes) {
  const base = { tree: "Дерево", bush: "Кустарник", furniture: "Объект" }[category] ?? "Объект";
  const num = id.match(/^(\d+)/)?.[1];
  const name = num ? `${base} ${num}` : `${base} ${id}`;
  if (!measured) return name;
  const traits = [SIZE_WORD[classes.size_class], CROWN_WORD[classes.crown_class]]
    .filter(Boolean)
    .join(", ");
  return `${name} — ${traits}, ${measured.height} м`;
}

function parseArgs(argv) {
  const args = argv.slice(2);
  if (args.length === 0 || args[0].startsWith("--")) {
    console.error("Использование: node tools/convert_models.mjs <папка-с-моделями> [--category tree]");
    process.exit(1);
  }
  const source = resolve(args[0]);
  const categoryIndex = args.indexOf("--category");
  const category = categoryIndex >= 0 ? args[categoryIndex + 1] : "tree";
  if (!CATEGORY_DEFAULTS[category]) {
    console.error(`Неизвестная категория: ${category}. Доступны: ${Object.keys(CATEGORY_DEFAULTS).join(", ")}`);
    process.exit(1);
  }
  return { source, category };
}

async function findObjFiles(dir) {
  const found = [];
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (extname(entry.name).toLowerCase() === ".obj") found.push(full);
    }
  }
  await walk(dir);
  return found.sort();
}

// Имя файла -> безопасный id: латиница/цифры/подчёркивания в нижнем регистре.
function toId(filePath) {
  return basename(filePath, extname(filePath))
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

// Проверяем конвертер один раз на старте. Без этого при недоступном npm-реестре
// npx висит на КАЖДОМ файле по несколько минут, и на паке из 200 моделей это
// выглядит как зависший скрипт без единого сообщения.
async function resolveConverter() {
  for (const candidate of [["obj2gltf"], ["npx", "--no-install", "obj2gltf"]]) {
    try {
      await execFileAsync(candidate[0], [...candidate.slice(1), "--version"], { timeout: 20_000 });
      return candidate;
    } catch {
      // пробуем следующий вариант
    }
  }
  console.error("Не найден obj2gltf — конвертер OBJ -> glTF.");
  console.error("Установите его: npm install -g obj2gltf");
  process.exit(1);
}

async function main() {
  const { source, category } = parseArgs(process.argv);
  const converter = await resolveConverter();

  try {
    await stat(source);
  } catch {
    console.error(`Папка не найдена: ${source}`);
    process.exit(1);
  }

  const objFiles = await findObjFiles(source);
  if (objFiles.length === 0) {
    console.error(`В ${source} не найдено ни одного .obj.`);
    console.error("Если в паке только FBX/BLEND -- сконвертируйте их в OBJ (Blender: File > Export > Wavefront .obj).");
    process.exit(1);
  }
  console.log(`Найдено .obj: ${objFiles.length}`);

  await mkdir(MODELS_DIR, { recursive: true });

  const defaults = CATEGORY_DEFAULTS[category];
  const models = [];
  const catalogEntries = [];
  const seenIds = new Set();
  let failed = 0;

  for (const [index, objPath] of objFiles.entries()) {
    let id = toId(objPath);
    // Имена в паках нередко повторяются в разных подпапках -- разводим суффиксом.
    if (seenIds.has(id)) id = `${id}_${index}`;
    seenIds.add(id);

    const outPath = join(MODELS_DIR, `${id}.glb`);
    try {
      // --separateTextures: текстура выносится файлом рядом и переиспользуется
      // всеми моделями пака (у них общий атлас). С вшитой текстурой каждая
      // модель весила бы в 2+ раза больше, а браузер качал бы одно и то же.
      await execFileAsync(
        converter[0],
        [...converter.slice(1), "-i", objPath, "-o", outPath, "--binary", "--separateTextures"],
        { maxBuffer: 64 * 1024 * 1024 }
      );
    } catch (e) {
      failed += 1;
      console.warn(`  ! не сконвертировалось: ${basename(objPath)} — ${e.message.split("\n")[0]}`);
      continue;
    }

    const measured = await measureObj(objPath);
    const classes = classify(measured);
    const url = `/models/${id}.glb`;
    models.push(url);
    catalogEntries.push({
      id,
      category,
      label: toLabel(id, category, measured, classes),
      size_class: classes.size_class,
      crown_class: classes.crown_class,
      setback_kind: defaults.setback_kind,
      object_type: defaults.object_type,
      model: url,
      dimensions: {
        height: measured?.height ?? defaults.height,
        radius: measured?.radius ?? defaults.radius,
        // Высота ствола из геометрии не вычисляется -- нужна она только
        // примитиву-заглушке, а у этой записи заглушка не используется:
        // модель есть.
        trunk_height: defaults.trunk_height,
        width: null,
        depth: null,
      },
      render: { shape: defaults.shape, color: defaults.color },
    });

    if ((index + 1) % 25 === 0) console.log(`  ...${index + 1}/${objFiles.length}`);
  }

  await writeFile(MANIFEST_PATH, JSON.stringify({ models }, null, 2), "utf8");
  await writeFile(GENERATED_CATALOG_PATH, JSON.stringify(catalogEntries, null, 2), "utf8");

  console.log(`\nГотово: ${models.length} моделей${failed ? `, не удалось: ${failed}` : ""}`);
  console.log(`  .glb            -> ${MODELS_DIR}`);
  console.log(`  манифест        -> ${MANIFEST_PATH}`);
  console.log(`  записи каталога -> ${GENERATED_CATALOG_PATH}`);
  console.log("\nБэкенд отдаст новые записи сразу: каталог перечитывается на каждый запрос /api/catalog.");
  console.log("Во фронтенде обновите страницу.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
