import { CheckCircle2, CircleSlash, Download, RotateCcw, Square, XCircle } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useJobs } from "@/hooks/useJobs";
import { api, assetUrl, TERMINAL_STATES, type Job } from "@/lib/api";
import { STATE_LABEL, TASK_LABEL, timeAgo } from "@/lib/format";

const tone = (j: Job) =>
  j.state === "completed" ? "success" : j.state === "failed" ? "danger" : j.state === "interrupted" ? "warning" : j.state === "canceled" ? "neutral" : "accent";

export function JobRow({ job }: { job: Job }) {
  const { upsert } = useJobs();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const terminal = TERMINAL_STATES.has(job.state);
  const retryable = ["failed", "canceled", "interrupted"].includes(job.state);

  const act = async (fn: () => Promise<Job>) => {
    setBusy(true);
    setError(undefined);
    try {
      upsert(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const Icon = job.state === "completed" ? CheckCircle2 : job.state === "failed" ? XCircle : job.state === "canceled" ? CircleSlash : null;

  return (
    <li className="rounded-[var(--radius-card)] border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {Icon && <Icon className="size-4 text-muted" aria-hidden />}
            <span className="font-medium">{TASK_LABEL[job.task] ?? job.task}</span>
            <Badge tone={tone(job)}>{STATE_LABEL[job.state]}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted">
            Started {timeAgo(job.created_at)}
            {job.retry_of && " · retry"}
          </p>
        </div>
        <div className="flex gap-2">
          {!terminal && (
            <Button size="sm" variant="secondary" disabled={busy || job.cancel_requested} onClick={() => act(() => api.cancelJob(job.id))}>
              <Square aria-hidden /> {job.cancel_requested ? "Stopping…" : "Cancel"}
            </Button>
          )}
          {retryable && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => act(() => api.retryJob(job.id))}>
              <RotateCcw aria-hidden /> Retry
            </Button>
          )}
        </div>
      </div>

      {!terminal && (
        <div className="mt-3">
          <Progress value={job.state === "processing" ? job.progress : null} label={`${STATE_LABEL[job.state]} progress`} />
          {job.stage_detail && <p className="mt-1 text-xs text-muted">{job.stage_detail}</p>}
        </div>
      )}

      {job.error_message && job.state !== "canceled" && <p className="mt-3 text-sm text-fg/90">{job.error_message}</p>}
      {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}

      {job.state === "completed" &&
        job.outputs.map((id) => (
          <div key={id} className="mt-3 flex flex-wrap items-center gap-3">
            <audio controls preload="none" src={assetUrl(id)} className="h-10 max-w-full" aria-label="Result playback" />
            <Button size="sm" variant="ghost" onClick={() => (window.location.href = assetUrl(id, true))}>
              <Download aria-hidden /> Download
            </Button>
          </div>
        ))}
    </li>
  );
}
