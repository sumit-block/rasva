import { ClipboardPaste } from "lucide-react";
import { Button } from "@/components/ui/button";
import { readClipboardImage } from "@/lib/paste";
import { cn } from "@/lib/utils";

/** Reads a picture from the clipboard; ⌘V on the page does the same without a click. */
export function PasteButton({ onFile, onError, disabled, className, label = "Paste" }: {
  onFile: (f: File) => void;
  onError: (message: string) => void;
  disabled?: boolean;
  className?: string;
  label?: string;
}) {
  return (
    <Button type="button" variant="secondary" disabled={disabled} className={cn(className)}
      title="Paste a copied picture (or press ⌘V / Ctrl+V)"
      onClick={() => void readClipboardImage().then(onFile, (e: Error) => onError(e.message))}>
      <ClipboardPaste aria-hidden /> {label}
    </Button>
  );
}
