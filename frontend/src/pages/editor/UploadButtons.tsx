import type { ReactNode } from "react";
import { FolderUp, Loader2, UploadCloud } from "lucide-react";
import { Button, type ButtonProps } from "../../components/ui/button";

// Кнопка-метка над скрытым <input type="file">: клик по ней открывает выбор
// файла. value сбрасывается, чтобы повторный выбор того же файла тоже
// приходил событием change.
function FilePickButton({
  icon,
  loading,
  disabled,
  size,
  variant,
  className,
  title,
  onFiles,
  inputProps,
  children,
}: {
  icon: ReactNode;
  loading: boolean;
  disabled: boolean;
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
  className?: string;
  title?: string;
  onFiles: (files: File[]) => void;
  inputProps: Record<string, unknown>;
  children: ReactNode;
}) {
  return (
    <label title={title} className={className}>
      <Button asChild variant={variant ?? "outline"} size={size} disabled={disabled} className="w-full">
        <span className={disabled ? "pointer-events-none opacity-50" : "cursor-pointer"}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
          {children}
        </span>
      </Button>
      <input
        type="file"
        hidden
        disabled={disabled}
        {...inputProps}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onFiles(files);
          e.target.value = "";
        }}
      />
    </label>
  );
}

type UploadButtonProps = {
  loading: boolean;
  disabled: boolean;
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
  className?: string;
};

// Один чертёж или несколько файлов объекта сразу: топосъёмка, сети,
// граница заказа -- DXF, DWG или zip-архив с ними.
export function UploadDrawingButton({ onFiles, ...props }: UploadButtonProps & { onFiles: (files: File[]) => void }) {
  return (
    <FilePickButton
      {...props}
      icon={<UploadCloud className="h-4 w-4" />}
      title="DXF, DWG или ZIP. Можно выбрать несколько файлов объекта сразу -- топосъёмку, сети, границу заказа: они сольются в одну сцену"
      inputProps={{ accept: ".dxf,.dwg,.zip", multiple: true }}
      onFiles={onFiles}
    >
      Загрузить чертёж
    </FilePickButton>
  );
}

// webkitdirectory -- выбор папки целиком (в типах React его нет).
export function UploadDwgFolderButton({ onFiles, ...props }: UploadButtonProps & { onFiles: (files: File[]) => void }) {
  return (
    <FilePickButton
      {...props}
      icon={<FolderUp className="h-4 w-4" />}
      title="Папка проекта: .dwg, .dxf и .zip сливаются в одну сцену на сервере (DWG конвертируются в DXF)"
      inputProps={{ webkitdirectory: "", multiple: true }}
      onFiles={onFiles}
    >
      Загрузить папку
    </FilePickButton>
  );
}
