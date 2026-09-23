import { Loader2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";

export function SaveAsDialog({
  open,
  onOpenChange,
  name,
  onNameChange,
  busy,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  name: string;
  onNameChange: (value: string) => void;
  busy: boolean;
  onSave: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Сохранить как новый проект</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSave();
          }}
          className="flex flex-col gap-3"
        >
          <Input placeholder="Название проекта" value={name} onChange={(e) => onNameChange(e.target.value)} autoFocus />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={!name.trim() || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Сохранить"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
