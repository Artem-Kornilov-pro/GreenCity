import { useCallback, useRef, useState, type DragEvent } from "react";

// Все файлы из записи перетаскивания: у папки -- рекурсивно. readEntries
// отдаёт содержимое папки порциями (по 100 в Chrome), читаем до пустой.
async function filesFromEntry(entry: FileSystemEntry): Promise<File[]> {
  if (entry.isFile) {
    return new Promise((resolve) => (entry as FileSystemFileEntry).file((f) => resolve([f]), () => resolve([])));
  }
  if (!entry.isDirectory) return [];
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const files: File[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve) => reader.readEntries(resolve, () => resolve([])));
    if (batch.length === 0) return files;
    for (const child of batch) files.push(...(await filesFromEntry(child)));
  }
}

async function droppedFiles(dataTransfer: DataTransfer): Promise<File[]> {
  // Записи надо взять синхронно, до первого await: после него браузер
  // очищает dataTransfer.
  const entries = Array.from(dataTransfer.items)
    .map((item) => item.webkitGetAsEntry())
    .filter((e): e is FileSystemEntry => e !== null);
  if (entries.length === 0) return Array.from(dataTransfer.files);
  return (await Promise.all(entries.map(filesFromEntry))).flat();
}

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

// Перетаскивание файлов и папок в окно редактора. dragging -- для подложки
// «Отпустите, чтобы загрузить».
export function useFileDrop(onFiles: (files: File[]) => void, enabled = true) {
  const [dragging, setDragging] = useState(false);
  // dragenter/dragleave приходят и от дочерних элементов -- считаем глубину,
  // иначе подложка мигает при движении курсора над панелями.
  const depth = useRef(0);

  const onDragEnter = useCallback(
    (e: DragEvent) => {
      if (!enabled || !hasFiles(e)) return;
      e.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    [enabled],
  );

  // preventDefault -- всегда, даже пока идёт загрузка: иначе брошенный файл
  // браузер откроет вместо редактора.
  const onDragOver = useCallback(
    (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = enabled ? "copy" : "none";
    },
    [enabled],
  );

  const onDragLeave = useCallback(() => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      if (!enabled) return;
      void droppedFiles(e.dataTransfer).then((files) => {
        if (files.length > 0) onFiles(files);
      });
    },
    [enabled, onFiles],
  );

  return { dragging, dropHandlers: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
