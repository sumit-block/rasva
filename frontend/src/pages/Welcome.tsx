/**
 * First-run setup (PRD §8, P0-011). Full-screen, resumable (step stored on the
 * Mac), skippable at any point. Nothing large downloads without an explicit
 * Install click that shows size and licence first.
 */
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Download, Lock, Play, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { api, assetUrl, isUsable, TERMINAL_STATES, type EngineInfo, type Job } from "@/lib/api";
import { formatBytes, STATE_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

const STEPS = ["Welcome", "Your Mac", "Storage", "Voice engine", "First narration", "Phone access", "Done"] as const;
const GB = 1024 ** 3;
const SAMPLE = {
  en: "Welcome to Rasva Studio. Everything you create here stays on your Mac.",
  hi: "रसवा स्टूडियो में आपका स्वागत है। आपकी हर रचना आपके मैक पर ही रहती है।",
};

function StepWelcome() {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Welcome to Rasva Studio</h1>
      <p className="mt-3 text-muted">
        Create narration in Hindi, English and Hinglish, and later edit images, using AI that runs entirely on this Mac.
      </p>
      <ul className="mt-6 space-y-3 text-sm">
        {[
          "Your scripts, voices and photos never leave this Mac.",
          "After setup, creating doesn't need the internet.",
          "Nothing large is downloaded without asking you first.",
        ].map((t) => (
          <li key={t} className="flex gap-3"><Lock className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />{t}</li>
        ))}
      </ul>
    </>
  );
}

function StepMac() {
  const check = useAsync(api.check);
  const ICON = { pass: CheckCircle2, warn: AlertTriangle, fail: XCircle } as const;
  const COLOR = { pass: "text-success", warn: "text-warning", fail: "text-danger" } as const;
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Checking your Mac</h1>
      {check.error && <Notice tone="error" className="mt-4">{check.error.message}</Notice>}
      {!check.data && !check.error && <p className="mt-4 text-sm text-muted">Checking…</p>}
      <ul className="mt-5 space-y-3">
        {check.data?.checks.map((c) => {
          const Icon = ICON[c.status];
          return (
            <li key={c.id} className="flex gap-3 text-sm">
              <Icon className={cn("mt-0.5 size-4 shrink-0", COLOR[c.status])} aria-label={c.status} />
              <div><p className="font-medium">{c.label}</p><p className="text-muted">{c.message}</p></div>
            </li>
          );
        })}
      </ul>
      {check.data && !check.data.ready && (
        <Notice tone="error" className="mt-4">Some checks failed. You can continue, but AI engines may not run.</Notice>
      )}
    </>
  );
}

function StepStorage() {
  const storage = useAsync(() => api.storage(true));
  const [gb, setGb] = useState<number>(50);
  const [saved, setSaved] = useState<string>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (storage.data) setGb(Math.round(storage.data.budget_bytes / GB));
  }, [storage.data]);
  const s = storage.data;
  const save = async () => {
    setError(undefined);
    try {
      await api.updateSettings({ "storage.budget_bytes": gb * GB });
      setSaved(`Rasva will use up to ${gb} GB.`);
      await storage.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">How much space can Rasva use?</h1>
      <p className="mt-3 text-sm text-muted">
        Voice and image engines, your uploads and results all count toward this limit. Rasva also always leaves
        {s ? ` ${formatBytes(s.os_reserve_bytes)} ` : " some space "}free for macOS.
      </p>
      {s && (
        <>
          <label className="mt-6 block text-sm">
            <span className="mb-2 flex justify-between font-medium">
              Storage limit <span className="tabular-nums">{gb} GB</span>
            </span>
            <input type="range" min={5} max={Math.max(5, Math.min(200, Math.floor((s.free_disk_bytes + s.used_bytes) / GB)))} value={gb}
              onChange={(e) => { setGb(Number(e.target.value)); setSaved(undefined); }}
              className="w-full accent-[var(--accent)]" />
          </label>
          <p className="mt-2 text-xs text-muted">
            {formatBytes(s.free_disk_bytes)} free on this disk now. 50 GB is enough for the voice engines and one
            image engine.
          </p>
          <Button variant="secondary" className="mt-4" onClick={save} disabled={gb * GB === s.budget_bytes}>Save limit</Button>
          {saved && <p className="mt-2 text-sm text-success" role="status">{saved}</p>}
        </>
      )}
      {(error || storage.error) && <Notice tone="error" className="mt-4">{error ?? storage.error?.message}</Notice>}
    </>
  );
}

function StepEngine({ engine, job, onInstall }: { engine: EngineInfo | undefined; job: Job | undefined; onInstall: () => Promise<void> }) {
  const [error, setError] = useState<string>();
  const running = job && !TERMINAL_STATES.has(job.state);
  if (!engine) return <p className="text-sm text-muted">Loading engines…</p>;
  const usable = isUsable(engine);
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Install a voice engine</h1>
      <div className="mt-5 rounded-[var(--radius-card)] border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-medium">{engine.display_name}</span>
          <Badge tone={engine.license_class === "permissive" ? "success" : "warning"}>{engine.license}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted">{engine.summary}</p>
        <dl className="mt-3 grid grid-cols-2 gap-1 text-xs">
          <dt className="text-muted">Download</dt><dd>{engine.download_bytes ? formatBytes(engine.download_bytes) : "–"}</dd>
          <dt className="text-muted">Space needed</dt><dd>about {formatBytes(engine.approx_installed_bytes)}</dd>
          <dt className="text-muted">Voices</dt><dd>{engine.voices.length || "–"}</dd>
        </dl>
        {engine.license_notes.map((n) => <p key={n} className="mt-2 text-xs text-muted">{n}</p>)}
      </div>
      {usable && <p className="mt-4 flex items-center gap-2 text-sm text-success"><CheckCircle2 className="size-4" aria-hidden /> Installed and ready.</p>}
      {!usable && !engine.compatibility.compatible && (
        <Notice className="mt-4">{engine.compatibility.reasons.join(" ")} You can finish setup now and install it later from Models.</Notice>
      )}
      {running && job && (
        <div className="mt-4" aria-live="polite">
          <Progress value={job.state === "downloading" ? job.progress : null} label="Installing" />
          <p className="mt-1 text-xs text-muted">{job.stage_detail ?? STATE_LABEL[job.state]}</p>
        </div>
      )}
      {job?.state === "failed" && <Notice tone="error" className="mt-4">{job.error_message}</Notice>}
      {error && <Notice tone="error" className="mt-4">{error}</Notice>}
      {!usable && engine.compatibility.compatible && !running && (
        <Button className="mt-4" onClick={() => onInstall().catch((e) => setError((e as Error).message))}>
          <Download aria-hidden /> {engine.needs_license_confirmation ? "Accept licence and install" : `Install ${engine.display_name}`}
        </Button>
      )}
    </>
  );
}

function StepFirst({ engine }: { engine: EngineInfo | undefined }) {
  const { jobs, upsert } = useJobs();
  const [lang, setLang] = useState<"en" | "hi">("en");
  const [text, setText] = useState(SAMPLE.en);
  const [jobId, setJobId] = useState<string>();
  const [error, setError] = useState<string>();
  const job = jobs.find((j) => j.id === jobId);
  const voice = engine?.voices.find((v) => v.language === lang);

  if (!engine || !isUsable(engine)) {
    return (
      <>
        <h1 className="text-2xl font-semibold tracking-tight">Your first narration</h1>
        <p className="mt-3 text-sm text-muted">This step needs a voice engine. Once one is installed, Audio Studio is ready for you.</p>
      </>
    );
  }
  const go = async () => {
    setError(undefined);
    try {
      const existing = (await api.projects({ q: "My first narration" })).find((p) => p.name === "My first narration");
      const project = existing ?? (await api.createProject({ name: "My first narration", module: "audio" }));
      const j = await api.createJob({ project_id: project.id, task: "audio.narrate", engine_id: engine.id,
        inputs: { text, voice_id: voice?.id, preview: true } });
      upsert(j);
      setJobId(j.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Your first narration</h1>
      <p className="mt-3 text-sm text-muted">Edit the sentence if you like, then hear it.</p>
      <div role="tablist" aria-label="Language" className="mt-4 inline-flex rounded-lg border border-border bg-surface-2 p-1">
        {(["en", "hi"] as const).filter((l) => engine.voices.some((v) => v.language === l)).map((l) => (
          <button key={l} role="tab" aria-selected={lang === l} onClick={() => { setLang(l); setText(SAMPLE[l]); }}
            className={cn("min-h-9 rounded-md px-3 text-sm", lang === l ? "bg-surface text-fg shadow-sm" : "text-muted")}>
            {l === "en" ? "English" : "Hindi"}
          </button>
        ))}
      </div>
      <label htmlFor="sample" className="sr-only">Sample text</label>
      <textarea id="sample" value={text} onChange={(e) => setText(e.target.value)} rows={3}
        className="mt-3 w-full rounded-lg border border-border bg-bg p-3 text-base" />
      <Button className="mt-3" onClick={go} disabled={!text.trim() || (job && !TERMINAL_STATES.has(job.state))}>
        <Play aria-hidden /> Hear it ({voice?.display_name})
      </Button>
      {job && !TERMINAL_STATES.has(job.state) && <Progress className="mt-4" value={job.state === "processing" ? job.progress : null} label="Generating" />}
      {job?.state === "completed" && job.outputs[0] && (
        <audio controls autoPlay src={assetUrl(job.outputs[0])} className="mt-4 w-full" aria-label="Your first narration" />
      )}
      {job?.state === "failed" && <Notice tone="error" className="mt-4">{job.error_message}</Notice>}
      {error && <Notice tone="error" className="mt-4">{error}</Notice>}
    </>
  );
}

function StepPhone() {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Use Rasva from your phone</h1>
      <p className="mt-3 text-sm text-muted">
        Starting jobs from your phone over home Wi-Fi needs secure pairing, which is coming in a later update. For now
        Rasva only accepts connections from this Mac. Nothing is exposed to your network.
      </p>
    </>
  );
}

function StepDone() {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">You're all set</h1>
      <p className="mt-3 text-sm text-muted">
        Write a script in Audio Studio, preview a sentence, then create the full narration. You can change any of these
        choices later in Settings.
      </p>
    </>
  );
}

export function WelcomePage() {
  const navigate = useNavigate();
  const settings = useAsync(api.settings);
  const models = useAsync(api.models);
  const { jobs, upsert } = useJobs();
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (settings.data) setStep(Math.min(settings.data["onboarding.step"] ?? 0, STEPS.length - 1));
  }, [settings.data]);

  const engine = useMemo(() => {
    const narrators = models.data?.filter((m) => m.tasks.includes("audio.narrate")) ?? [];
    return narrators.find(isUsable) ?? narrators.find((m) => m.compatibility.compatible) ?? narrators[0];
  }, [models.data]);
  const installJob = jobs.find((j) => j.engine_id === engine?.id && j.task === "model.install");
  const finishedInstall = installJob?.state === "completed";
  useEffect(() => {
    if (finishedInstall) void models.reload();
  }, [finishedInstall, models]);

  const goto = (n: number) => {
    setStep(n);
    void api.updateSettings({ "onboarding.step": n }).catch(() => undefined);
  };
  const finish = async (to: string) => {
    await api.updateSettings({ "onboarding.completed": true, "onboarding.step": 0 }).catch(() => undefined);
    navigate(to);
  };
  const install = async () => {
    if (!engine) return;
    upsert(await api.installModel(engine.id, engine.needs_license_confirmation));
  };

  const last = step === STEPS.length - 1;
  return (
    <div className="min-h-full bg-bg px-4 py-8 md:py-16">
      <div className="mx-auto max-w-xl">
        <div className="mb-8 flex items-center justify-between">
          <span className="flex items-center gap-2 font-semibold"><img src="/favicon.svg" alt="" className="size-6" /> Rasva Studio</span>
          {!last && <Button variant="ghost" size="sm" onClick={() => void finish("/")}>Skip setup</Button>}
        </div>
        <ol className="mb-8 flex gap-1" aria-label="Setup progress">
          {STEPS.map((label, i) => (
            <li key={label} className={cn("h-1.5 flex-1 rounded-full", i <= step ? "bg-accent" : "bg-surface-2")}>
              <span className="sr-only">{label}{i === step ? " (current step)" : ""}</span>
            </li>
          ))}
        </ol>
        <p className="mb-2 text-xs text-muted">Step {step + 1} of {STEPS.length} · {STEPS[step]}</p>

        <section aria-live="polite">
          {step === 0 && <StepWelcome />}
          {step === 1 && <StepMac />}
          {step === 2 && <StepStorage />}
          {step === 3 && <StepEngine engine={engine} job={installJob} onInstall={install} />}
          {step === 4 && <StepFirst engine={engine} />}
          {step === 5 && <StepPhone />}
          {step === 6 && <StepDone />}
        </section>

        <div className="mt-10 flex justify-between gap-3">
          {step > 0 ? <Button variant="ghost" onClick={() => goto(step - 1)}><ArrowLeft aria-hidden /> Back</Button> : <span />}
          {last ? (
            <Button onClick={() => void finish(engine && isUsable(engine) ? "/audio" : "/")}>
              {engine && isUsable(engine) ? "Open Audio Studio" : "Go to Home"}
            </Button>
          ) : (
            <Button onClick={() => goto(step + 1)}>
              {step === 3 && engine && !isUsable(engine) ? "Install later" : "Continue"} <ArrowRight aria-hidden />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
