import { Download } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { assetUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { exportPngUrl, imageApi } from "./api";
import { commentText, encodeJpeg, saveBytes, withComment } from "./jpeg";

/**
 * PNG (as made) or JPEG (quality setting), with or without generation details.
 * Details are a short label: AI-generated, engine, model revision and seed.
 * Your description is never written into the file.
 */
export function ExportPanel({ assetId, seed }: { assetId: string; seed?: unknown }) {
  const [format, setFormat] = useState<"png" | "jpeg">("png");
  const [quality, setQuality] = useState(90);
  const [details, setDetails] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const qid = useId();

  const save = async () => {
    setError(undefined);
    if (format === "png") {
      window.location.href = exportPngUrl(assetId, details);
      return;
    }
    setBusy(true);
    try {
      let bytes = await encodeJpeg(assetUrl(assetId), quality / 100);
      if (details) {
        const { details: label } = await imageApi.provenance(assetId);
        if (Object.keys(label).length) bytes = withComment(bytes, commentText(label));
      }
      const stem = seed !== undefined && seed !== null ? `rasva-image-seed-${String(seed)}` : `rasva-image-${assetId.slice(-8)}`;
      saveBytes(bytes, `${stem}.jpg`, "image/jpeg");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className="rounded-lg border border-border p-3">
      <legend className="px-1 text-sm font-medium">Download</legend>
      <div role="radiogroup" aria-label="File type" className="flex flex-wrap gap-2">
        {(["png", "jpeg"] as const).map((f) => (
          <button key={f} type="button" role="radio" aria-checked={format === f} onClick={() => setFormat(f)}
            className={cn("min-h-11 rounded-lg border px-3 text-left text-sm", format === f ? "border-accent bg-accent/10" : "border-border")}>
            <span className="block font-medium">{f === "png" ? "PNG" : "JPEG"}</span>
            <span className="block text-xs text-muted">{f === "png" ? "Exactly as made" : "Smaller file"}</span>
          </button>
        ))}
      </div>
      {format === "jpeg" && (
        <label htmlFor={qid} className="mt-3 block text-sm">
          <span className="mb-1 flex justify-between font-medium">Quality <span className="text-muted tabular-nums">{quality}</span></span>
          <input id={qid} type="range" min={50} max={100} step={5} value={quality}
            onChange={(e) => setQuality(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
        </label>
      )}
      <label className="mt-3 flex min-h-11 items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={details} onChange={(e) => setDetails(e.target.checked)} />
        <span>
          Include generation details
          <span className="block text-xs text-muted">
            Labels the file as AI-generated, with the engine and seed. Your description is never included.
            {!details && " Without details, the file carries no label that it is AI-generated."}
          </span>
        </span>
      </label>
      {error && <Notice tone="error" className="mt-2">{error}</Notice>}
      <Button className="mt-3" size="sm" disabled={busy} onClick={() => void save()}>
        <Download aria-hidden /> {busy ? "Making JPEG…" : `Download ${format === "png" ? "PNG" : "JPEG"}`}
      </Button>
    </fieldset>
  );
}
