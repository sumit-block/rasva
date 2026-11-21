/**
 * Paste a picture into a photo slot, besides uploading a file.
 *
 * - ⌘V / Ctrl+V anywhere on the page goes to one slot: the one the pointer is
 *   over or that last had focus, otherwise the first slot on the page. Pasting
 *   into a text field still pastes text there.
 * - A Paste button reads the clipboard directly (navigator.clipboard.read),
 *   for phones and for people who don't use the shortcut. Browsers ask first.
 *
 * Only the picture's pixels come along; the server checks type and size as it
 * does for uploads.
 */
import { useCallback, useEffect, useId, useRef } from "react";

export const PASTE_TYPES = ["image/png", "image/jpeg", "image/webp"];

type Target = { id: string; el: () => HTMLElement | null; onFile: (f: File) => void; disabled: () => boolean };

const targets = new Map<string, Target>();
let active: string | null = null;
let listening = false;

function stamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function named(blob: Blob): File {
  const type = PASTE_TYPES.includes(blob.type) ? blob.type : "image/png";
  const ext = type === "image/jpeg" ? "jpg" : type.split("/")[1];
  return new File([blob], `pasted-${stamp()}.${ext}`, { type });
}

/** The first picture in a paste event's data, or null (text, files of other types). */
export function imageFromClipboard(data: DataTransfer | null): File | null {
  if (!data) return null;
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const f = item.getAsFile();
      if (f) return named(f);
    }
  }
  return null;
}

export class PasteError extends Error {}

/** Read a picture from the clipboard (Paste button). */
export async function readClipboardImage(): Promise<File> {
  if (!navigator.clipboard?.read) {
    throw new PasteError("This browser can't read pictures from the clipboard. Press ⌘V (Ctrl+V on Windows) instead.");
  }
  let items: ClipboardItems;
  try {
    items = await navigator.clipboard.read();
  } catch {
    throw new PasteError("The browser didn't allow reading the clipboard. Press ⌘V (Ctrl+V on Windows) instead.");
  }
  for (const item of items) {
    const type = item.types.find((t) => PASTE_TYPES.includes(t)) ?? item.types.find((t) => t.startsWith("image/"));
    if (type) return named(await item.getType(type));
  }
  throw new PasteError("There's no picture on the clipboard. Copy a photo first, then paste.");
}

const editable = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && !(el instanceof HTMLInputElement && el.type === "file"));

function inDomOrder(): Target[] {
  return [...targets.values()]
    .filter((t) => t.el() && !t.disabled())
    .sort((a, b) => (a.el()!.compareDocumentPosition(b.el()!) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
}

function onPaste(e: ClipboardEvent) {
  if (editable(e.target)) return; // text fields keep normal paste
  const file = imageFromClipboard(e.clipboardData);
  if (!file) return;
  const current = active ? targets.get(active) : undefined;
  const target = current && current.el() && !current.disabled() ? current : inDomOrder()[0];
  if (!target) return;
  e.preventDefault();
  target.onFile(file);
}

/**
 * Make an element a paste slot. Spread the returned props on its wrapper.
 * `onFile` receives the pasted picture as a File, like a file input would.
 */
export function usePasteTarget<E extends HTMLElement = HTMLDivElement>(onFile: (f: File) => void, disabled = false) {
  const id = useId();
  const ref = useRef<E>(null);
  const latest = useRef({ onFile, disabled });
  latest.current = { onFile, disabled };

  useEffect(() => {
    targets.set(id, { id, el: () => ref.current, onFile: (f) => latest.current.onFile(f), disabled: () => latest.current.disabled });
    if (!listening) {
      document.addEventListener("paste", onPaste);
      listening = true;
    }
    return () => {
      targets.delete(id);
      if (active === id) active = null;
      if (targets.size === 0 && listening) {
        document.removeEventListener("paste", onPaste);
        listening = false;
      }
    };
  }, [id]);

  const claim = useCallback(() => {
    active = id;
  }, [id]);

  return { ref, onPointerEnter: claim, onFocusCapture: claim, "data-paste-target": "" } as const;
}
