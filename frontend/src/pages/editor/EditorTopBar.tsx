import { Link, useNavigate } from "react-router-dom";
import { Download, FolderKanban, FolderUp, LassoSelect, Leaf, Loader2, LogOut, Save, Sparkles, Trees, UploadCloud, User } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { useAuth } from "../../context/useAuth";

// Топбар плавает поверх сцены (не занимает место в потоке), поэтому сквозь
// него видна и блюрится сама 3D-сцена, а не плоский фон страницы -- без этого
// эффект "жидкого стекла" на однотонном фоне почти не заметен.
export function EditorTopBar({
  title,
  dxfLoading,
  dwgLoading,
  projectLoading,
  onDxfFile,
  onDwgFolder,
  sceneLoaded,
  selectionMode,
  onToggleSelection,
  assistantOpen,
  onToggleAssistant,
  greenPlanBusy,
  onGreenPlan,
  exportingDxf,
  onExportDxf,
  onExportJson,
  isSavedProject,
  projectBusy,
  onSave,
}: {
  title: string;
  // Раздельно: раньше один общий флаг крутил загрузку на обеих кнопках,
  // хотя грузилась одна. Пока идёт любая загрузка, обе кнопки выключены.
  dxfLoading: boolean;
  dwgLoading: boolean;
  projectLoading: boolean;
  onDxfFile: (file: File) => void;
  onDwgFolder: (files: FileList) => void;
  sceneLoaded: boolean;
  selectionMode: boolean;
  onToggleSelection: () => void;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
  greenPlanBusy: boolean;
  onGreenPlan: () => void;
  exportingDxf: boolean;
  onExportDxf: () => void;
  onExportJson: () => void;
  isSavedProject: boolean;
  projectBusy: boolean;
  onSave: () => void;
}) {
  const navigate = useNavigate();
  const { session, logout } = useAuth();
  const busy = dxfLoading || dwgLoading || projectLoading;

  return (
    <header className="absolute inset-x-0 top-0 z-40 flex h-14 items-center gap-3 border-b border-ink-200/60 bg-white/85 px-4 shadow-sm backdrop-blur-xl backdrop-saturate-150">
      <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold text-ink-900">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
          <Leaf className="h-4 w-4" />
        </span>
        <span className="hidden sm:inline">GreenCity</span>
      </Link>

      <div className="mx-2 h-5 w-px bg-ink-200" />

      <span className="truncate text-sm font-medium text-ink-700">{title}</span>

      <div className="ml-auto flex items-center gap-1.5">
        <label>
          <Button asChild variant="outline" size="sm" disabled={busy}>
            <span className={busy ? "pointer-events-none opacity-50" : "cursor-pointer"}>
              {dxfLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
              Загрузить DXF
            </span>
          </Button>
          <input
            type="file"
            accept=".dxf"
            hidden
            disabled={busy}
            onChange={(e) => {
              if (e.target.files?.[0]) onDxfFile(e.target.files[0]);
              e.target.value = ""; // тот же файл ещё раз -- тоже событие change
            }}
          />
        </label>

        <label title="Выбрать папку проекта с исходными .dwg -- каждый файл конвертируется в DXF на сервере и сливается в одну сцену (issue #50)">
          <Button asChild variant="outline" size="sm" disabled={busy}>
            <span className={busy ? "pointer-events-none opacity-50" : "cursor-pointer"}>
              {dwgLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderUp className="h-3.5 w-3.5" />}
              Загрузить DWG (папка)
            </span>
          </Button>
          <input
            type="file"
            // webkitdirectory -- нестандартный, но широко поддерживаемый
            // атрибут (Chrome/Firefox/Edge; Safari частично) для выбора
            // папки целиком вместо отдельных файлов.
            // @ts-expect-error -- webkitdirectory отсутствует в типах React для input
            webkitdirectory=""
            multiple
            hidden
            disabled={busy}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) onDwgFolder(e.target.files);
              e.target.value = "";
            }}
          />
        </label>

        {sceneLoaded && (
          <>
            <Button
              size="sm"
              variant={selectionMode ? "primary" : "outline"}
              onClick={onToggleSelection}
              title="Обвести участок на плане мышкой -- потом можно сослаться на него в правке текстом («посади здесь кусты»)"
            >
              <LassoSelect className="h-3.5 w-3.5" />
              {selectionMode ? "Обводите на плане…" : "Выделить зону"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onToggleAssistant}
              aria-pressed={assistantOpen}
              className={assistantOpen ? "border-brand-300 bg-brand-50" : undefined}
            >
              <Sparkles className="h-3.5 w-3.5" />
              Ассистент
            </Button>
            {/* Главное действие редактора -- зелёная кнопка. */}
            <Button size="sm" variant="primary" onClick={onGreenPlan} disabled={greenPlanBusy} title="Автоозеленение по прошлым проектам (GreenPlan)">
              {greenPlanBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trees className="h-3.5 w-3.5" />}
              GreenPlan
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" disabled={exportingDxf}>
                  {exportingDxf ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                  Экспорт
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem onClick={onExportDxf}>Экспорт в DXF</DropdownMenuItem>
                <DropdownMenuItem onClick={onExportJson}>Экспорт в JSON</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {session && (
              <Button size="sm" variant="outline" onClick={onSave} disabled={projectBusy}>
                {projectBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                {isSavedProject ? "Сохранить" : "Сохранить как…"}
              </Button>
            )}
          </>
        )}

        <div className="mx-1 h-5 w-px bg-ink-200" />

        {session ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm">
                <User className="h-3.5 w-3.5" />
                {session.username}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>{session.username}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => navigate("/projects")}>
                <FolderKanban className="h-4 w-4" />
                Мои проекты
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  logout();
                  navigate("/");
                }}
              >
                <LogOut className="h-4 w-4" />
                Выйти
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/login">Войти</Link>
            </Button>
            <Button asChild size="sm">
              <Link to="/register">Регистрация</Link>
            </Button>
          </>
        )}
      </div>
    </header>
  );
}
