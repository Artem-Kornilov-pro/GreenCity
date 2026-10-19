// Отдать браузеру файл на сохранение, не уходя со страницы. Ссылка -- в
// документе (Firefox не скачивает по ссылке вне него), без target: blob
// нашего же адреса с download сохраняется, а не открывается. Адрес blob
// отзывается не сразу: Safari открывал файл во вкладке вместо загрузки,
// если адрес отозван до начала скачивания, -- окно уходило с редактора.
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
