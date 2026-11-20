import type { JobState } from "./api";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 10 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function timeAgo(iso: string): string {
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(Math.round(s), "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

/** Plain-language job stages (PRD §21.4): never imply pause/resume. */
export const STATE_LABEL: Record<JobState, string> = {
  draft: "Draft",
  queued: "Waiting",
  downloading: "Downloading",
  preparing: "Preparing",
  loading_model: "Starting engine",
  processing: "Generating",
  finalizing: "Saving",
  completed: "Ready",
  failed: "Failed",
  canceled: "Canceled",
  interrupted: "Interrupted",
};

export const TASK_LABEL: Record<string, string> = {
  "diagnostic.tone": "Pipeline check",
  "audio.narrate": "Narration",
  "audio.assemble": "Chapter export",
  "image.generate": "Image",
  "image.edit": "Image edit",
  "model.install": "Install engine",
  "model.remove": "Remove engine",
};

export const CATEGORY_LABEL: Record<string, string> = {
  audio_models: "Voice engines",
  image_models: "Image engines",
  environments: "Engine software",
  references: "Your uploads",
  outputs: "Generated files",
  caches: "Caches & temp files",
  database_logs: "Database & logs",
};

export function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}
