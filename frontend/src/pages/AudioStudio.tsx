import { AudioLines, Play, Plus, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { JobRow } from "@/components/JobRow";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import {
  api,
  chapters,
  isUsable,
  pronunciations,
  TERMINAL_STATES,
  type AppliedPronunciation,
  type Chapter,
  type EngineInfo,
  type Project,
  type Voice,
} from "@/lib/api";
import { ChapterPanel } from "@/components/audio/ChapterPanel";
import { ExportPanel } from "@/components/audio/ExportPanel";
import { MixPanel } from "@/components/audio/MixPanel";
import { hasDevanagari } from "@/lib/script";
import { cn } from "@/lib/utils";

const MAX_CHARS = 200_000; // per chapter; each sentence is a separate job
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

/** Shows which words the pronunciation dictionary will change (PRD §10.2 step 6). */
function PronunciationCheck({ text, language, projectId, enabled, onToggle }: {
  text: string; language: "hi" | "en" | undefined; projectId: string | null; enabled: boolean; onToggle: (v: boolean) => void;
}) {
  const [result, setResult] = useState<{ applied: AppliedPronunciation[]; pending: AppliedPronunciation[] }>();
  useEffect(() => {
    if (!language || !text.trim()) {
      setResult(undefined);
      return;
    }
    const t = window.setTimeout(() => {
      pronunciations
        .check({ text, language, ...(projectId ? { project_id: projectId } : {}) })
        .then((r) => setResult({ applied: r.applied, pending: r.unapproved_matches }), () => setResult(undefined));
    }, 600);
    return () => window.clearTimeout(t);
  }, [text, language, projectId]);

  const unique = (xs: AppliedPronunciation[]) => [...new Map(xs.map((a) => [`${a.written}→${a.spoken}`, a])).values()];
  const applied = unique(result?.applied ?? []);
  const pending = unique(result?.pending ?? []);
  return (
    <div className="mt-3 rounded-lg border border-border bg-surface-2/50 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex min-h-9 items-center gap-2">
          <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
          Use my pronunciations
        </label>
        <Link to="/pronunciation" className="text-xs text-accent underline">Manage</Link>
      </div>
      {enabled && applied.length > 0 && (
        <p className="mt-2 text-xs text-muted">
          Will read:{" "}
          {applied.map((a) => (
            <span key={a.written + a.spoken} className="mr-2 inline-block text-fg">{a.written} → {a.spoken}</span>
          ))}
        </p>
      )}
      {pending.length > 0 && (
        <p className="mt-1 text-xs text-warning">
          Not yet approved: {pending.map((a) => a.written).join(", ")}. Approve them on the Pronunciation page to use them.
        </p>
      )}
      {enabled && applied.length === 0 && pending.length === 0 && text.trim() && (
        <p className="mt-2 text-xs text-muted">No dictionary words in this script.</p>
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

  const chapterList = useAsync(() => (projectId ? chapters.list(projectId) : Promise.resolve([])), [projectId]);
  const chapterId = params.get("chapter") ?? chapterList.data?.[0]?.id ?? null;
  const [chapter, setChapter] = useState<Chapter>();

  const [script, setScript] = useState("");
  const [voiceId, setVoiceId] = useState<string>(engine.voices[0]?.id ?? "");
  const [speed, setSpeed] = useState(1);
  const [useDictionary, setUseDictionary] = useState(true);
  const [saved, setSaved] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const loadedFor = useRef<string | null>(null);
  const lastSaved = useRef("");
  const creating = useRef<Promise<string> | null>(null);

  const snapshot = (sc: string, v: string, sp: number, d: boolean) => JSON.stringify([sc, v, sp, d]);

  // Load the selected chapter, and keep it fresh while it is generating or exporting.
  useEffect(() => {
    if (!chapterId) {
      setChapter(undefined);
      return;
    }
    let alive = true;
    const load = () => chapters.get(chapterId).then((c) => alive && setChapter(c), () => undefined);
    void load();
    return () => {
      alive = false;
    };
  }, [chapterId]);
  const busy = !!chapter && ((chapter.summary.counts.generating ?? 0) > 0 ||
    chapter.exports.some((e) => !TERMINAL_STATES.has(e.state)));
  useEffect(() => {
    if (!busy || !chapterId) return;
    const t = window.setInterval(() => chapters.get(chapterId).then(setChapter, () => undefined), 2000);
    return () => window.clearInterval(t);
  }, [busy, chapterId]);

  // Put the chapter's script and voice into the editor once per chapter. A project
  // without chapters starts from its older single-script draft, if any.
  useEffect(() => {
    if (chapter && loadedFor.current !== chapter.id) {
      loadedFor.current = chapter.id;
      setScript(chapter.script);
      setVoiceId(chapter.settings.voice_id);
      setSpeed(chapter.settings.speed);
      setUseDictionary(chapter.settings.use_dictionary);
      lastSaved.current = snapshot(chapter.script, chapter.settings.voice_id, chapter.settings.speed, chapter.settings.use_dictionary);
      setSaved("idle");
    } else if (!chapterId && project && chapterList.data && loadedFor.current !== `project:${project.id}`) {
      loadedFor.current = `project:${project.id}`;
      setScript(typeof project.settings.script === "string" ? project.settings.script : "");
      lastSaved.current = "";
    }
  }, [chapter, chapterId, project, chapterList.data]);

  const createProject = async () => {
    const p = await api.createProject({ name: `Narration ${new Date().toLocaleDateString()}`, module: "audio" });
    await reloadProjects();
    return p;
  };

  /** The chapter for the text in the editor, created (with project) on first use. */
  const ensureChapter = (): Promise<string> => {
    if (chapterId) return Promise.resolve(chapterId);
    creating.current ??= (async () => {
      const target = project ?? (await createProject());
      const created = await chapters.create(target.id, {
        title: "Chapter 1", script,
        settings: { engine_id: engine.id, voice_id: voiceId, speed, use_dictionary: useDictionary },
      });
      loadedFor.current = created.id; // keep the typed draft
      lastSaved.current = snapshot(script, voiceId, speed, useDictionary);
      setChapter(created);
      setParams({ project: target.id, chapter: created.id });
      await chapterList.reload();
      return created.id;
    })().catch((e) => {
      creating.current = null;
      setError((e as Error).message);
      throw e;
    });
    return creating.current;
  };

  const save = async () => {
    const id = await ensureChapter();
    const snap = snapshot(script, voiceId, speed, useDictionary);
    if (snap === lastSaved.current) return id;
    const updated = await chapters.update(id, {
      script, settings: { engine_id: engine.id, voice_id: voiceId, speed, use_dictionary: useDictionary },
    });
    lastSaved.current = snap;
    setChapter(updated);
    return id;
  };

  // Autosave (PRD §26): shortly after typing stops. Creates the project and
  // chapter on the first keystroke so nothing is lost.
  useEffect(() => {
    if (!script.trim() && !chapterId) return;
    if (chapterId && loadedFor.current !== chapterId) return;
    if (snapshot(script, voiceId, speed, useDictionary) === lastSaved.current) return;
    setSaved("saving");
    const t = window.setTimeout(() => {
      save().then(() => setSaved("saved"), () => setSaved("error"));
    }, 1000);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [script, voiceId, speed, useDictionary, chapterId]);

  const voice = engine.voices.find((v) => v.id === voiceId);
  const devanagariWithEnglish = voice?.language === "en" && hasDevanagari(script);
  const tooLong = script.length > MAX_CHARS;
  const blocked = !script.trim() || !voice || devanagariWithEnglish || tooLong || submitting;

  const run = async (fn: () => Promise<void>) => {
    setSubmitting(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const preview = () =>
    run(async () => {
      await ensureChapter();
      const target = projects.find((p) => p.id === (params.get("project") ?? projectId)) ?? project;
      const pid = target?.id ?? chapter?.project_id;
      if (!pid) return;
      upsert(await api.createJob({
        project_id: pid, task: "audio.narrate", engine_id: engine.id,
        // Only the first sentence is spoken; send a bounded excerpt.
        inputs: { text: script.slice(0, 1000), voice_id: voiceId, speed, preview: true, use_dictionary: useDictionary },
      }));
    });

  const generateAll = () =>
    run(async () => {
      const id = await save();
      setChapter(await chapters.generate(id, "needed"));
    });

  const newChapter = () =>
    run(async () => {
      const target = project ?? (await createProject());
      const n = (chapterList.data?.length ?? 0) + 1;
      const created = await chapters.create(target.id, {
        title: `Chapter ${n}`, script: "",
        settings: { engine_id: engine.id, voice_id: voiceId, speed, use_dictionary: useDictionary },
      });
      await chapterList.reload();
      setParams({ project: target.id, chapter: created.id });
    });

  const previews = useMemo(
    () => jobs.filter((j) => j.task === "audio.narrate" && j.project_id === (project?.id ?? chapter?.project_id) && j.inputs.preview),
    [jobs, project, chapter],
  );
  const pendingCount = chapter
    ? (chapter.summary.counts.pending ?? 0) + (chapter.summary.counts.failed ?? 0) + (chapter.summary.counts.outdated ?? 0)
    : 0;

  return (
    <>
      <PageHeader
        title="Audio Studio"
        description="Write a script, choose a voice, preview a sentence, then generate and export the narration."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="project">Project</label>
            <select id="project" value={projectId ?? ""} onChange={(e) => setParams({ project: e.target.value })}
              className="min-h-11 max-w-60 rounded-lg border border-border bg-surface px-3 text-sm">
              {projects.length === 0 && <option value="">New project</option>}
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button variant="secondary" onClick={() => void run(async () => { const p = await createProject(); setParams({ project: p.id }); })}>
              <Plus aria-hidden /> New
            </Button>
          </div>
        }
      />

      {(chapterList.data?.length ?? 0) > 0 && (
        <nav aria-label="Chapters" className="mb-4 flex gap-2 overflow-x-auto pb-1">
          {chapterList.data!.map((c) => (
            <button key={c.id} onClick={() => setParams({ project: projectId!, chapter: c.id })} aria-current={c.id === chapterId ? "page" : undefined}
              className={cn("min-h-11 shrink-0 rounded-lg border px-3 text-sm", c.id === chapterId ? "border-accent bg-accent/10 text-fg" : "border-border text-muted")}>
              {c.title}
            </button>
          ))}
          <Button variant="ghost" className="shrink-0" onClick={() => void newChapter()}><Plus aria-hidden /> Chapter</Button>
        </nav>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card>
          <div className="flex items-center justify-between">
            <CardTitle>Script</CardTitle>
            <span className="text-xs text-muted" aria-live="polite">
              {saved === "saving" ? "Saving…" : saved === "saved" ? "Saved on your Mac" : saved === "error" ? "Not saved" : ""}
            </span>
          </div>
          <label htmlFor="script" className="sr-only">Script</label>
          <textarea id="script" value={script} onChange={(e) => setScript(e.target.value)} rows={10}
            placeholder={"Paste or write your script. Hindi (देवनागरी), English or Roman Hinglish.\n\nBlank lines become longer pauses. Long chapters are fine: each sentence is generated separately."}
            className="mt-3 w-full resize-y rounded-lg border border-border bg-bg p-3 text-base leading-relaxed text-fg placeholder:text-muted" />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{hasDevanagari(script) ? "Contains Devanagari" : script.trim() ? "Roman letters" : ""}</span>
            <span className={cn(tooLong && "text-danger")}>{script.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}</span>
          </div>
          <PronunciationCheck text={script.slice(0, 20000)} language={voice?.language} projectId={projectId}
            enabled={useDictionary} onToggle={setUseDictionary} />
          {devanagariWithEnglish && (
            <Notice tone="error" className="mt-3">
              This script contains Devanagari, which English voices skip. Choose a Hindi voice, or write those words in Roman letters.
            </Notice>
          )}
          {tooLong && <Notice tone="error" className="mt-3">A chapter can hold up to {MAX_CHARS.toLocaleString()} characters. Split it into chapters.</Notice>}
          {error && <Notice tone="error" className="mt-3">{error}</Notice>}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" disabled={blocked} onClick={() => void preview()}>
              <Play aria-hidden /> Preview first sentence
            </Button>
            <Button disabled={blocked} onClick={() => void generateAll()}>
              <Sparkles aria-hidden /> {chapter && chapter.segments.some((s) => s.current) ? (pendingCount ? `Generate ${pendingCount} remaining` : "Generate changes") : "Generate narration"}
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>Voice</CardTitle>
          <CardDescription>{engine.display_name}: {engine.summary}</CardDescription>
          <div className="mt-4"><VoicePicker voices={engine.voices} value={voiceId} onChange={setVoiceId} /></div>
          {prefs.mode === "advanced" && engine.features.includes("speed") && (
            <label className="mt-4 block text-sm">
              <span className="mb-1 flex justify-between font-medium">Speed <span className="text-muted tabular-nums">{speed.toFixed(2)}×</span></span>
              <input type="range" min={0.5} max={2} step={0.05} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
            </label>
          )}
          {chapter && chapter.segments.some((s) => s.current) && (
            <p className="mt-4 text-xs text-muted">Changing the voice marks existing sentences as out of date; they keep playing until you update them.</p>
          )}
          <p className="mt-4 text-xs text-muted">Generated speech is labelled as synthetic in its file details.</p>
          {previews.length > 0 && (
            <ul className="mt-4 space-y-2">{previews.slice(0, 3).map((j) => <JobRow key={j.id} job={j} />)}</ul>
          )}
        </Card>
      </div>

      {chapter && chapter.segments.length > 0 && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <ChapterPanel chapter={chapter} onChange={setChapter} />
          <div className="space-y-4">
            <MixPanel chapter={chapter} onChange={setChapter} />
            <ExportPanel chapter={chapter} onExported={() => void chapters.get(chapter.id).then(setChapter)} />
          </div>
        </div>
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
