import { Check, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import {
  api,
  assetUrl,
  isUsable,
  pronunciations,
  TERMINAL_STATES,
  type EngineInfo,
  type EntryInput,
  type PronunciationEntry,
} from "@/lib/api";
import { hasDevanagari } from "@/lib/script";
import { cn } from "@/lib/utils";

const LANG_LABEL = { hi: "Hindi voices", en: "English voices", any: "All voices" } as const;

const input = "min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-sm";

function EntryForm({
  initial,
  projects,
  onSave,
  onCancel,
  existing = false,
}: {
  existing?: boolean;
  initial: EntryInput;
  projects: { id: string; name: string }[];
  onSave: (e: EntryInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [written, setWritten] = useState((initial.written_forms ?? []).join(", "));
  const [spoken, setSpoken] = useState(initial.spoken ?? "");
  const [language, setLanguage] = useState<EntryInput["language"]>(
    initial.language ?? (hasDevanagari(written) ? "hi" : "en"),
  );
  const [projectId, setProjectId] = useState(initial.project_id ?? "");
  const [hint, setHint] = useState(initial.phonetic_hint ?? "");
  const [examples, setExamples] = useState((initial.examples ?? []).join("\n"));
  const [wholeWord, setWholeWord] = useState(initial.whole_word ?? true);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await onSave({
        written_forms: written.split(",").map((w) => w.trim()).filter(Boolean),
        spoken,
        language,
        project_id: projectId || null,
        phonetic_hint: hint,
        examples: examples.split("\n").map((x) => x.trim()).filter(Boolean),
        whole_word: wholeWord,
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block font-medium">Written as</span>
        <input className={input} value={written} onChange={(e) => setWritten(e.target.value)} required
          placeholder="कृष्ण, Krishna, Krishn" />
        <span className="mt-1 block text-xs text-muted">Separate spellings with commas.</span>
      </label>
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block font-medium">Say it as</span>
        <input className={input} value={spoken} onChange={(e) => setSpoken(e.target.value)} required
          placeholder="How the voice should read it, e.g. क्रिश्न" />
        <span className="mt-1 block text-xs text-muted">
          Respell it the way it sounds, in the same script the voice reads. Preview it, then approve.
        </span>
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Use for</span>
        <select className={input} value={language} onChange={(e) => setLanguage(e.target.value as EntryInput["language"])}>
          {(["hi", "en", "any"] as const).map((l) => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}
        </select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Where</span>
        <select className={input} value={projectId} onChange={(e) => setProjectId(e.target.value)}
          disabled={existing} title={existing ? "Scope can't change after saving" : undefined}>
          <option value="">All projects</option>
          {projects.map((p) => <option key={p.id} value={p.id}>Only “{p.name}”</option>)}
        </select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Phonetic hint (optional)</span>
        <input className={input} value={hint} onChange={(e) => setHint(e.target.value)} placeholder="Kṛṣṇa" />
      </label>
      <label className="flex min-h-11 items-center gap-2 self-end text-sm">
        <input type="checkbox" checked={wholeWord} onChange={(e) => setWholeWord(e.target.checked)} />
        Whole words only
      </label>
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block font-medium">Example sentences (optional, one per line)</span>
        <textarea className="w-full rounded-lg border border-border bg-bg p-3 text-sm" rows={2} value={examples}
          onChange={(e) => setExamples(e.target.value)} />
      </label>
      {error && <Notice tone="error" className="sm:col-span-2">{error}</Notice>}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" disabled={busy}>Save</Button>
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function EntryCard({
  entry,
  engines,
  projects,
  onChanged,
}: {
  entry: PronunciationEntry;
  engines: EngineInfo[];
  projects: { id: string; name: string }[];
  onChanged: () => Promise<void>;
}) {
  const { jobs, upsert } = useJobs();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string>();
  const [jobId, setJobId] = useState<string>();
  const voices = engines.flatMap((e) =>
    e.voices.filter((v) => entry.language === "any" || v.language === entry.language).map((v) => ({ engine: e, voice: v })),
  );
  const [choice, setChoice] = useState(voices[0] ? `${voices[0].engine.id}|${voices[0].voice.id}` : "");
  const [example, setExample] = useState(-1);
  const job = jobs.find((j) => j.id === jobId);
  const project = projects.find((p) => p.id === entry.project_id);

  const run = async (fn: () => Promise<unknown>) => {
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const preview = () =>
    run(async () => {
      const [engine_id, voice_id] = choice.split("|");
      const j = await pronunciations.preview(entry.id, {
        engine_id: engine_id!, voice_id: voice_id!, ...(example >= 0 ? { example } : {}),
      });
      upsert(j);
      setJobId(j.id);
    });

  if (editing) {
    return (
      <Card>
        <EntryForm existing initial={entry} projects={projects} onCancel={() => setEditing(false)}
          onSave={async (body) => { await pronunciations.update(entry.id, body); setEditing(false); await onChanged(); }} />
      </Card>
    );
  }
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-lg">
          {entry.written_forms.join(", ")} <span className="text-muted">→</span>{" "}
          <span className="font-medium">{entry.spoken}</span>
        </p>
        <div className="flex flex-wrap gap-1">
          <Badge tone={entry.approved ? "success" : "warning"}>{entry.approved ? "Approved" : "Not approved"}</Badge>
          <Badge>{LANG_LABEL[entry.language]}</Badge>
          <Badge>{project ? `Only “${project.name}”` : "All projects"}</Badge>
        </div>
      </div>
      {entry.phonetic_hint && <p className="mt-1 text-xs text-muted">Hint: {entry.phonetic_hint}</p>}

      {voices.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor={`v-${entry.id}`}>Voice</label>
          <select id={`v-${entry.id}`} value={choice} onChange={(e) => setChoice(e.target.value)} className="min-h-11 rounded-lg border border-border bg-bg px-2 text-sm">
            {voices.map(({ engine, voice }) => (
              <option key={`${engine.id}|${voice.id}`} value={`${engine.id}|${voice.id}`}>{voice.display_name} ({engine.display_name})</option>
            ))}
          </select>
          {entry.examples.length > 0 && (
            <select aria-label="What to read" value={example} onChange={(e) => setExample(Number(e.target.value))} className="min-h-11 max-w-56 rounded-lg border border-border bg-bg px-2 text-sm">
              <option value={-1}>The word alone</option>
              {entry.examples.map((x, i) => <option key={x} value={i}>{x}</option>)}
            </select>
          )}
          <Button size="sm" variant="secondary" onClick={() => void preview()} disabled={job && !TERMINAL_STATES.has(job.state)}>
            <Play aria-hidden /> Preview
          </Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted">Install a voice engine for these voices to preview.</p>
      )}
      {job && !TERMINAL_STATES.has(job.state) && <Progress className="mt-3" value={null} label="Generating preview" />}
      {job?.state === "completed" && job.outputs[0] && <audio controls autoPlay src={assetUrl(job.outputs[0])} className="mt-3 w-full" />}
      {job?.state === "failed" && <Notice tone="error" className="mt-3">{job.error_message}</Notice>}
      {error && <Notice tone="error" className="mt-3">{error}</Notice>}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant={entry.approved ? "ghost" : "primary"}
          onClick={() => void run(async () => { await pronunciations.update(entry.id, { approved: !entry.approved }); await onChanged(); })}>
          <Check aria-hidden /> {entry.approved ? "Unapprove" : "Approve"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil aria-hidden /> Edit</Button>
        {confirmDelete ? (
          <>
            <Button size="sm" variant="danger" onClick={() => void run(async () => { await pronunciations.remove(entry.id); await onChanged(); })}>Delete</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Keep</Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 aria-hidden /> Delete</Button>
        )}
      </div>
    </Card>
  );
}

export function PronunciationPage() {
  const entries = useAsync(() => pronunciations.list());
  const suggestions = useAsync(pronunciations.suggestions);
  const models = useAsync(api.models);
  const projects = useAsync(() => api.projects({ module: "audio" }));
  const [draft, setDraft] = useState<EntryInput | null>(null);
  const [filter, setFilter] = useState("");

  const engines = (models.data ?? []).filter((m) => m.tasks.includes("audio.narrate") && isUsable(m));
  const projectList = (projects.data ?? []).map((p) => ({ id: p.id, name: p.name }));
  const reload = async () => {
    await Promise.all([entries.reload(), suggestions.reload()]);
  };
  const shown = useMemo(
    () => (entries.data ?? []).filter((e) => !filter || [...e.written_forms, e.spoken].some((w) => w.toLowerCase().includes(filter.toLowerCase()))),
    [entries.data, filter],
  );

  return (
    <>
      <PageHeader
        title="Pronunciation"
        description="Teach voices how to say names and words. Your script stays as you wrote it; only what the voice reads changes."
        actions={!draft && <Button onClick={() => setDraft({})}><Plus aria-hidden /> Add pronunciation</Button>}
      />

      {draft && (
        <Card className="mb-4">
          <CardTitle className="mb-3">New pronunciation</CardTitle>
          <EntryForm initial={draft} projects={projectList} onCancel={() => setDraft(null)}
            onSave={async (body) => { await pronunciations.create(body); setDraft(null); await reload(); }} />
        </Card>
      )}

      {suggestions.data && suggestions.data.length > 0 && (
        <Card className="mb-4">
          <CardTitle>Suggested names</CardTitle>
          <CardDescription>Names that are often mispronounced. Tap one to add how it should sound.</CardDescription>
          <div className="mt-3 flex flex-wrap gap-2">
            {suggestions.data.map((s) => (
              <button key={s.written} onClick={() => setDraft({ written_forms: [s.written], phonetic_hint: s.hint, language: "hi" })}
                className="min-h-11 rounded-lg border border-border px-3 text-sm hover:border-accent">
                {s.written} <span className="text-xs text-muted">{s.hint}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {(entries.data?.length ?? 0) > 5 && (
        <input type="search" aria-label="Search pronunciations" placeholder="Search" value={filter}
          onChange={(e) => setFilter(e.target.value)} className={cn(input, "mb-4 bg-surface")} />
      )}
      {entries.error && <Notice tone="error">{entries.error.message}</Notice>}
      {entries.data?.length === 0 && !draft ? (
        <EmptyState icon={<Plus />} title="No pronunciations yet" action={<Button onClick={() => setDraft({})}>Add pronunciation</Button>}>
          Add a name, respell how it should sound, preview it with a voice, then approve it. Approved pronunciations
          are used in every narration.
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {shown.map((e) => (
            <li key={e.id}><EntryCard entry={e} engines={engines} projects={projectList} onChanged={reload} /></li>
          ))}
        </ul>
      )}
    </>
  );
}
