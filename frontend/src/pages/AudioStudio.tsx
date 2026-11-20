import { AudioLines, Play, Plus, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { JobRow } from "@/components/JobRow";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import { api, isUsable, type EngineInfo, type Project, type Voice } from "@/lib/api";
import { hasDevanagari } from "@/lib/script";
import { cn } from "@/lib/utils";

const MAX_CHARS = 5000;
const LANG_LABEL = { en: "English", hi: "Hindi" } as const;

function VoicePicker({ voices, value, onChange }: { voices: Voice[]; value: string; onChange: (id: string) => void }) {
  const languages = [...new Set(voices.map((v) => v.language))];
  const current = voices.find((v) => v.id === value);
  const [lang, setLang] = useState<Voice["language"]>(current?.language ?? languages[0] ?? "en");
  useEffect(() => {
    if (current) setLang(current.language);
  }, [current]);
  return (
    <div>
      <div role="tablist" aria-label="Voice language" className="mb-3 inline-flex rounded-lg border border-border bg-surface-2 p-1">
        {languages.map((l) => (
          <button
            key={l}
            role="tab"
            aria-selected={lang === l}
            onClick={() => setLang(l)}
            className={cn("min-h-9 rounded-md px-3 text-sm", lang === l ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg")}
          >
            {LANG_LABEL[l]}
          </button>
        ))}
      </div>
      <div role="radiogroup" aria-label="Voice" className="grid grid-cols-2 gap-2">
        {voices
          .filter((v) => v.language === lang)
          .map((v) => (
            <button
              key={v.id}
              role="radio"
              aria-checked={value === v.id}
              onClick={() => onChange(v.id)}
              className={cn(
                "min-h-14 rounded-lg border px-3 py-2 text-left transition-colors",
                value === v.id ? "border-accent bg-accent/10" : "border-border hover:bg-surface-2",
              )}
            >
              <span className="block text-sm font-medium">{v.display_name}</span>
              <span className="block text-xs text-muted">
                {[v.accent, v.gender].filter(Boolean).join(" · ") || LANG_LABEL[v.language]}
              </span>
            </button>
          ))}
      </div>
      {lang === "hi" && (
        <p className="mt-2 text-xs text-muted">
          Hindi voices are basic in this first engine. A stronger Hindi engine and a pronunciation dictionary come next.
        </p>
      )}
    </div>
  );
}

function Studio({ engine, projects, reloadProjects }: { engine: EngineInfo; projects: Project[]; reloadProjects: () => Promise<void> }) {
  const [params, setParams] = useSearchParams();
  const { jobs, upsert } = useJobs();
  const { prefs } = usePrefs();
  const projectId = params.get("project") ?? projects[0]?.id ?? null;
  const project = projects.find((p) => p.id === projectId);

  const [script, setScript] = useState("");
  const [voiceId, setVoiceId] = useState<string>(engine.voices[0]?.id ?? "");
  const [speed, setSpeed] = useState(1);
  const [saved, setSaved] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const loadedFor = useRef<string | null>(null);
  const lastSaved = useRef<string>("");

  // Load the project's draft once per project.
  useEffect(() => {
    if (!project || loadedFor.current === project.id) return;
    loadedFor.current = project.id;
    const s = project.settings;
    setScript(typeof s.script === "string" ? s.script : "");
    if (typeof s.voice_id === "string" && engine.voices.some((v) => v.id === s.voice_id)) setVoiceId(s.voice_id);
    if (typeof s.speed === "number") setSpeed(s.speed);
    lastSaved.current = JSON.stringify({
      script: typeof s.script === "string" ? s.script : "",
      voice_id: s.voice_id,
      speed: s.speed,
    });
    setSaved("idle");
  }, [project, engine.voices]);

  // Autosave the draft (PRD §26) shortly after typing stops. A first-time user
  // has no project yet: create one on the first keystroke so nothing is lost.
  useEffect(() => {
    if (!project) {
      if (script.trim()) void ensureProject();
      return;
    }
    if (loadedFor.current !== project.id) return;
    const draft = { script, voice_id: voiceId, speed };
    const snapshot = JSON.stringify(draft);
    if (snapshot === lastSaved.current) return;
    setSaved("saving");
    const t = window.setTimeout(() => {
      api.updateProject(project.id, { settings: draft }).then(
        () => {
          lastSaved.current = snapshot;
          setSaved("saved");
        },
        () => setSaved("error"),
      );
    }, 800);
    return () => window.clearTimeout(t);
  }, [script, voiceId, speed, project]);

  const voice = engine.voices.find((v) => v.id === voiceId);
  const devanagariWithEnglish = voice?.language === "en" && hasDevanagari(script);
  const tooLong = script.length > MAX_CHARS;
  const blocked = !script.trim() || !voice || devanagariWithEnglish || tooLong || submitting;

  const creating = useRef<Promise<Project> | null>(null);

  /** Create a project. With `keepDraft`, the text already typed becomes its script. */
  const createProject = async (keepDraft = false) => {
    const name = `Narration ${new Date().toLocaleDateString()}`;
    const p = await api.createProject({ name, module: "audio" });
    if (keepDraft) loadedFor.current = p.id; // don't overwrite the typed draft with empty settings
    await reloadProjects();
    setParams({ project: p.id });
    return p;
  };

  const ensureProject = () => {
    if (project) return Promise.resolve(project);
    creating.current ??= createProject(true).catch((e) => {
      creating.current = null;
      setError((e as Error).message);
      throw e;
    });
    return creating.current;
  };

  const generate = async (preview: boolean) => {
    setSubmitting(true);
    setError(undefined);
    try {
      const target = await ensureProject();
      upsert(
        await api.createJob({
          project_id: target.id,
          task: "audio.narrate",
          engine_id: engine.id,
          inputs: { text: script, voice_id: voiceId, speed, preview },
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const results = useMemo(
    () => jobs.filter((j) => j.task === "audio.narrate" && j.project_id === projectId),
    [jobs, projectId],
  );

  return (
    <>
      <PageHeader
        title="Audio Studio"
        description="Write a script, choose a voice, preview a sentence, then create the full narration."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="project">Project</label>
            <select
              id="project"
              value={projectId ?? ""}
              onChange={(e) => setParams({ project: e.target.value })}
              className="min-h-11 max-w-60 rounded-lg border border-border bg-surface px-3 text-sm"
            >
              {projects.length === 0 && <option value="">New project</option>}
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <Button variant="secondary" onClick={() => void createProject()}>
              <Plus aria-hidden /> New
            </Button>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Script</CardTitle>
            <span className="text-xs text-muted" aria-live="polite">
              {saved === "saving" ? "Saving…" : saved === "saved" ? "Saved on your Mac" : saved === "error" ? "Not saved" : ""}
            </span>
          </div>
          <label htmlFor="script" className="sr-only">Script</label>
          <textarea
            id="script"
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={10}
            placeholder={"Paste or write your script. Hindi (देवनागरी), English or Roman Hinglish.\n\nBlank lines become longer pauses."}
            className="mt-3 w-full resize-y rounded-lg border border-border bg-bg p-3 text-base leading-relaxed text-fg placeholder:text-muted"
          />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{hasDevanagari(script) ? "Contains Devanagari" : script.trim() ? "Roman letters" : ""}</span>
            <span className={cn(tooLong && "text-danger")}>
              {script.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}
            </span>
          </div>
          {devanagariWithEnglish && (
            <Notice tone="error" className="mt-3">
              This script contains Devanagari, which English voices skip. Choose a Hindi voice, or write those words in
              Roman letters.
            </Notice>
          )}
          {tooLong && (
            <Notice tone="error" className="mt-3">
              This engine narrates up to {MAX_CHARS.toLocaleString()} characters at a time for now. Long-form chapters
              are coming.
            </Notice>
          )}
          {error && <Notice tone="error" className="mt-3">{error}</Notice>}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" disabled={blocked} onClick={() => void generate(true)}>
              <Play aria-hidden /> Preview first sentence
            </Button>
            <Button disabled={blocked} onClick={() => void generate(false)}>
              <Sparkles aria-hidden /> Create narration
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>Voice</CardTitle>
          <CardDescription>{engine.display_name}: {engine.summary}</CardDescription>
          <div className="mt-4">
            <VoicePicker voices={engine.voices} value={voiceId} onChange={setVoiceId} />
          </div>
          {prefs.mode === "advanced" && engine.features.includes("speed") && (
            <label className="mt-4 block text-sm">
              <span className="mb-1 flex justify-between font-medium">
                Speed <span className="text-muted tabular-nums">{speed.toFixed(2)}×</span>
              </span>
              <input
                type="range"
                min={0.5}
                max={2}
                step={0.05}
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="w-full accent-[var(--accent)]"
              />
            </label>
          )}
          <p className="mt-4 text-xs text-muted">Generated speech is labelled as synthetic in its file details.</p>
        </Card>
      </div>

      <h2 className="mt-8 mb-3 text-lg font-semibold">Results</h2>
      {results.length === 0 ? (
        <p className="text-sm text-muted">Previews and narrations for this project appear here.</p>
      ) : (
        <ul className="space-y-3" aria-live="polite">
          {results.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
        </ul>
      )}
    </>
  );
}

export function AudioStudioPage() {
  const navigate = useNavigate();
  const models = useAsync(api.models);
  const projects = useAsync(() => api.projects({ module: "audio" }));

  const engine = models.data?.find((m) => m.tasks.includes("audio.narrate") && isUsable(m));
  const candidate = models.data?.find((m) => m.tasks.includes("audio.narrate"));

  if (models.error || projects.error) {
    return <Notice tone="error">{(models.error ?? projects.error)?.message}</Notice>;
  }
  if (!models.data || !projects.data) {
    return <p className="text-sm text-muted">Loading…</p>;
  }
  if (!engine) {
    return (
      <>
        <PageHeader title="Audio Studio" />
        <EmptyState
          icon={<AudioLines />}
          title="Install a voice engine to start"
          action={<Button onClick={() => navigate("/models")}>Go to Models</Button>}
        >
          {candidate && !candidate.compatibility.compatible
            ? candidate.compatibility.reasons.join(" ")
            : "Narration needs a voice engine on this Mac. Kokoro is small and quick to install."}
        </EmptyState>
      </>
    );
  }
  return <Studio engine={engine} projects={projects.data} reloadProjects={projects.reload} />;
}
