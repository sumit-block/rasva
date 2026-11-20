import { AlertTriangle, AudioLines, CheckCircle2, HardDrive, Play, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { runPipelineCheck } from "@/lib/actions";
import { api, isUsable } from "@/lib/api";
import { formatBytes, timeAgo } from "@/lib/format";

const CHECK_ICON = { pass: CheckCircle2, warn: AlertTriangle, fail: XCircle } as const;
const CHECK_COLOR = { pass: "text-success", warn: "text-warning", fail: "text-danger" } as const;

export function HomePage() {
  const check = useAsync(api.check);
  const storage = useAsync(() => api.storage());
  const projects = useAsync(() => api.projects());
  const models = useAsync(api.models);
  const settings = useAsync(api.settings);
  const navigate = useNavigate();
  // First launch: start the guided setup (PRD §8). It can be skipped at any step.
  useEffect(() => {
    if (settings.data && !settings.data["onboarding.completed"]) navigate("/welcome", { replace: true });
  }, [settings.data, navigate]);
  const narrationReady = !!models.data?.some((m) => m.tasks.includes("audio.narrate") && isUsable(m));
  const { upsert } = useJobs();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();

  const onCheck = async () => {
    setRunning(true);
    setError(undefined);
    try {
      upsert(await runPipelineCheck());
      navigate("/jobs");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const s = storage.data;
  const recent = (projects.data ?? []).slice(0, 5);

  return (
    <>
      <PageHeader title="Welcome to Rasva Studio" description="Create narration and images on your Mac. Your files never leave it." />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-start gap-4">
            <div className="rounded-xl bg-accent/12 p-3 text-accent">
              <AudioLines className="size-6" aria-hidden />
            </div>
            <div className="flex-1">
              <CardTitle className="text-lg">Create narration</CardTitle>
              <CardDescription>
                Turn a Hindi, English or Hinglish script into speech.
                {!narrationReady && " Install a voice engine from Models to begin."}
              </CardDescription>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={() => navigate(narrationReady ? "/audio" : "/models")} aria-describedby="narration-soon">
                  {narrationReady ? "Create narration" : "Install a voice engine"}
                </Button>
                <Button variant="secondary" onClick={onCheck} disabled={running}>
                  <Play aria-hidden /> {running ? "Starting…" : "Run a quick system test"}
                </Button>
              </div>
              <p id="narration-soon" className="mt-2 text-xs text-muted">
                The quick test plays a short tone to confirm your Mac can run and save jobs.
              </p>
              {error && <Notice tone="error" className="mt-3">{error}</Notice>}
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Storage</CardTitle>
            <HardDrive className="size-4 text-muted" aria-hidden />
          </div>
          {s ? (
            <>
              <p className="mt-3 text-2xl font-semibold">{formatBytes(s.remaining_bytes)}</p>
              <p className="text-sm text-muted">left of {formatBytes(s.budget_bytes)}</p>
              <Progress
                className="mt-3"
                value={s.ratio}
                label="Storage used"
                tone={s.level === "ok" ? "accent" : s.level === "warning" ? "warning" : "danger"}
              />
              {s.level !== "ok" && (
                <p className="mt-2 text-xs text-warning">
                  Storage is getting full. <Link className="underline" to="/settings">Review usage</Link>
                </p>
              )}
            </>
          ) : (
            <p className="mt-3 text-sm text-muted">{storage.error?.message ?? "Measuring…"}</p>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle>Recent projects</CardTitle>
          {recent.length === 0 ? (
            <CardDescription>Your projects will appear here.</CardDescription>
          ) : (
            <ul className="mt-3 divide-y divide-border">
              {recent.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 py-3">
                  <span className="truncate">{p.name}</span>
                  <span className="shrink-0 text-xs text-muted">{timeAgo(p.updated_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Mac readiness</CardTitle>
            {check.data && <Badge tone={check.data.ready ? "success" : "danger"}>{check.data.ready ? "Ready" : "Needs attention"}</Badge>}
          </div>
          {check.error && <Notice tone="error" className="mt-3">{check.error.message}</Notice>}
          <ul className="mt-3 space-y-3">
            {check.data?.checks.map((c) => {
              const Icon = CHECK_ICON[c.status];
              return (
                <li key={c.id} className="flex gap-3 text-sm">
                  <Icon className={`mt-0.5 size-4 shrink-0 ${CHECK_COLOR[c.status]}`} aria-label={c.status} />
                  <div>
                    <p className="font-medium">{c.label}</p>
                    <p className="text-muted">{c.message}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      </div>
    </>
  );
}
