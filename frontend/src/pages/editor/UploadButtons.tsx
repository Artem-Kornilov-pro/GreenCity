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

export function UploadDxfButton({ onFile, ...props }: UploadButtonProps & { onFile: (file: File) => void }) {
  return (
    <FilePickButton {...props} icon={<UploadCloud className="h-4 w-4" />} inputProps={{ accept: ".dxf" }} onFiles={(files) => onFile(files[0])}>
      Загрузить DXF
    </FilePickButton>
  );
}

// webkitdirectory -- выбор папки целиком (в типах React его нет).
export function UploadDwgFolderButton({ onFiles, ...props }: UploadButtonProps & { onFiles: (files: File[]) => void }) {
  return (
    <FilePickButton
      {...props}
      icon={<FolderUp className="h-4 w-4" />}
      title="Папка проекта с исходными .dwg -- каждый файл конвертируется в DXF на сервере и сливается в одну сцену"
      inputProps={{ webkitdirectory: "", multiple: true }}
      onFiles={onFiles}
    >
      Загрузить DWG (папка)
    </FilePickButton>
  );
}
