import { Boxes, Download, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { api, TERMINAL_STATES, type EngineInfo, type Job } from "@/lib/api";
import { formatBytes, STATE_LABEL } from "@/lib/format";

const LICENSE: Record<EngineInfo["license_class"], { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  permissive: { label: "Open source", tone: "success" },
  "open-weight": { label: "Open weights", tone: "warning" },
  restricted: { label: "Restricted licence", tone: "danger" },
  unknown: { label: "Licence unverified", tone: "danger" },
};

const INSTALL: Record<EngineInfo["install_state"], string> = {
  built_in: "Built in",
  not_installed: "Not installed",
  installing: "Installing",
  installed: "Installed",
  failed: "Install incomplete",
  removing: "Removing",
};

function ModelCard({ model, liveJob, onChanged }: { model: EngineInfo; liveJob: Job | undefined; onChanged: () => void }) {
  const { upsert } = useJobs();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [confirming, setConfirming] = useState<"license" | "remove" | null>(null);

  const job = liveJob ?? model.active_job ?? undefined;
  const running = job && !TERMINAL_STATES.has(job.state);
  const installed = model.install_state === "installed";
  const canInstall = model.install_state === "not_installed" || model.install_state === "failed";

  const act = async (fn: () => Promise<Job>) => {
    setBusy(true);
    setError(undefined);
    setConfirming(null);
    try {
      upsert(await fn());
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const install = (accept = false) => {
    if (model.needs_license_confirmation && !accept) {
      setConfirming("license");
      return;
    }
    void act(() => api.installModel(model.id, accept));
  };

  return (
    <Card className="flex h-full flex-col">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <CardTitle>{model.display_name}</CardTitle>
        <div className="flex gap-2">
          <Badge tone={LICENSE[model.license_class].tone} title={model.license}>
            {LICENSE[model.license_class].label}
          </Badge>
          <Badge tone={installed || model.install_state === "built_in" ? "success" : "neutral"}>
            {INSTALL[model.install_state]}
          </Badge>
        </div>
      </div>
      <CardDescription>{model.summary}</CardDescription>

      <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
        <dt className="text-muted">Licence</dt>
        <dd>{model.license}</dd>
        {model.install_state !== "built_in" && (
          <>
            <dt className="text-muted">{installed ? "Space used" : "Download"}</dt>
            <dd>{formatBytes(installed ? model.installed_bytes : model.download_bytes)}</dd>
          </>
        )}
        {canInstall && model.required_bytes > 0 && model.required_bytes < model.download_bytes && (
          <>
            <dt className="text-muted">Still to download</dt>
            <dd>{formatBytes(model.required_bytes)}</dd>
          </>
        )}
        {model.languages.length > 0 && (
          <>
            <dt className="text-muted">Languages</dt>
            <dd>{model.languages.join(", ")}</dd>
          </>
        )}
      </dl>

      {!model.compatibility.compatible && (
        <Notice tone="error" className="mt-3">{model.compatibility.reasons.join(" ")}</Notice>
      )}

      {running && job && (
        <div className="mt-4" aria-live="polite">
          <Progress
            value={job.state === "downloading" ? job.progress : null}
            label={`${model.display_name}: ${STATE_LABEL[job.state]}`}
          />
          <p className="mt-1 text-xs text-muted">{job.stage_detail ?? STATE_LABEL[job.state]}</p>
        </div>
      )}
      {job?.state === "failed" && job.error_message && !running && (
        <Notice tone="error" className="mt-3">{job.error_message}</Notice>
      )}
      {error && <Notice tone="error" className="mt-3">{error}</Notice>}

      {confirming === "license" && (
        <Notice className="mt-3">
          <p>
            {model.display_name} uses the licence “{model.license}”. It may limit how you can use what you create
            with it. Install anyway?
          </p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => install(true)}>I understand, install</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Not now</Button>
          </div>
        </Notice>
      )}
      {confirming === "remove" && (
        <Notice className="mt-3">
          <p>Remove {model.display_name}? Your projects and generated files are kept. You can reinstall later.</p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant="danger" onClick={() => act(() => api.removeModel(model.id))}>Remove</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Keep it</Button>
          </div>
        </Notice>
      )}

      <div className="mt-auto flex flex-wrap gap-2 pt-4">
        {running && job ? (
          <Button size="sm" variant="secondary" disabled={busy || job.cancel_requested} onClick={() => act(() => api.cancelJob(job.id))}>
            {job.cancel_requested ? "Stopping…" : "Cancel"}
          </Button>
        ) : (
          confirming === null && (
          <>
            {canInstall && model.compatibility.compatible && (
              <Button size="sm" disabled={busy} onClick={() => install()}>
                <Download aria-hidden /> {model.install_state === "failed" ? "Resume install" : "Install"}
              </Button>
            )}
            {(installed || model.install_state === "failed") && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming("remove")}>
                <Trash2 aria-hidden /> Remove
              </Button>
            )}
          </>
          )
        )}
      </div>
    </Card>
  );
}

export function ModelsPage() {
  const models = useAsync(api.models);
  const { jobs } = useJobs();

  // Refresh install state whenever an engine install/remove job finishes.
  const finished = jobs.filter((j) => j.task.startsWith("model.") && TERMINAL_STATES.has(j.state)).map((j) => j.id).join();
  const seen = useRef(finished);
  useEffect(() => {
    if (finished !== seen.current) {
      seen.current = finished;
      void models.reload();
    }
  }, [finished, models]);

  return (
    <>
      <PageHeader
        title="Models"
        description="Engines available on this Mac. Downloads are checked against a fingerprint before they're installed."
      />
      {models.error && <Notice tone="error">{models.error.message}</Notice>}
      {models.data?.length === 0 && (
        <EmptyState icon={<Boxes />} title="No engines yet">Engines appear here once they are added.</EmptyState>
      )}
      <ul className="grid gap-3 md:grid-cols-2">
        {models.data?.map((m) => (
          <li key={m.id}>
            <ModelCard
              model={m}
              liveJob={jobs.find((j) => j.engine_id === m.id && j.task.startsWith("model."))}
              onChanged={() => void models.reload()}
            />
          </li>
        ))}
      </ul>
    </>
  );
}
