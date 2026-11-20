import { AlertTriangle, CheckCircle2, Mic, Square, Trash2, Upload, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { assetUrl, voices, type Asset, type ReferenceQuality } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { startRecording, type Recording } from "@/lib/recorder";
import { cn } from "@/lib/utils";

const field = "min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-sm";
const PROMPT = {
  // Gender-neutral wording: anyone may read it.
  hi: "आज मौसम बहुत अच्छा है। यह छोटा सा अंश मेरी अपनी आवाज़ में है, ताकि रसवा मेरी आवाज़ को पहचान सके।",
  en: "Today the weather is lovely. I'm reading this short passage in my own voice so Rasva can learn how I sound.",
};

function QualityReport({ q }: { q: ReferenceQuality }) {
  const Icon = q.level === "good" ? CheckCircle2 : q.level === "warning" ? AlertTriangle : XCircle;
  const color = q.level === "good" ? "text-success" : q.level === "warning" ? "text-warning" : "text-danger";
  return (
    <div className="mt-3 rounded-lg border border-border p-3 text-sm">
      <p className={cn("flex items-center gap-2 font-medium", color)}>
        <Icon className="size-4" aria-hidden />
        {q.level === "good" ? "Sounds good" : q.level === "warning" ? "Usable, with tips" : "Please record again"}
        <span className="ml-auto text-xs text-muted">{(q.speech_s ?? q.duration_s).toFixed(1)} s of speech</span>
      </p>
      {q.issues.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{q.issues.map((i) => <li key={i.code}>{i.message}</li>)}</ul>
      )}
    </div>
  );
}

function NewVoice({ onDone }: { onDone: () => void }) {
  const statement = useAsync(voices.statement);
  const [step, setStep] = useState(0);
  const [basis, setBasis] = useState<"self" | "permission">("self");
  const [speaker, setSpeaker] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [language, setLanguage] = useState<"hi" | "en">("hi");
  const [recording, setRecording] = useState<Recording | null>(null);
  const [level, setLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [ref, setRef] = useState<{ asset: Asset; quality: ReferenceQuality } | null>(null);
  const [transcript, setTranscript] = useState(PROMPT.hi);
  const [confirmed, setConfirmed] = useState(false);
  const [name, setName] = useState("My voice");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const stopRef = useRef<() => void>(() => undefined);

  useEffect(() => () => recording?.cancel(), [recording]);
  useEffect(() => setTranscript(PROMPT[language]), [language]);

  const discard = async () => {
    if (ref) await voices.discardReference(ref.asset.id);
    setRef(null);
  };
  const upload = async (blob: Blob, filename: string) => {
    setBusy(true);
    setError(undefined);
    try {
      await discard();
      setRef(await voices.uploadReference(blob, filename));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const record = async () => {
    setError(undefined);
    try {
      const r = await startRecording((rms, s) => { setLevel(rms); setSeconds(s); }, () => stopRef.current());
      setRecording(r);
      stopRef.current = async () => {
        setRecording(null);
        await upload(await r.stop(), "recording.wav");
      };
    } catch (e) {
      setError((e as Error).message || "The microphone isn't available.");
    }
  };
  const save = async () => {
    if (!ref) return;
    setBusy(true);
    setError(undefined);
    try {
      await voices.create({
        name, language, reference_asset_id: ref.asset.id, transcript, transcript_confirmed: confirmed,
        consent: { basis, speaker_name: speaker, accepted },
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancelAll = async () => {
    recording?.cancel();
    await discard();
    onDone();
  };

  const steps = ["Consent", "Recording", "Transcript", "Name"];
  return (
    <Card>
      <ol className="mb-4 flex gap-1" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s} className={cn("h-1.5 flex-1 rounded-full", i <= step ? "bg-accent" : "bg-surface-2")}><span className="sr-only">{s}</span></li>
        ))}
      </ol>
      <p className="mb-2 text-xs text-muted">Step {step + 1} of 4 · {steps[step]}</p>

      {step === 0 && (
        <>
          <CardTitle>Whose voice is this?</CardTitle>
          <div role="radiogroup" className="mt-3 grid gap-2">
            {(["self", "permission"] as const).map((b) => (
              <button key={b} role="radio" aria-checked={basis === b} onClick={() => setBasis(b)}
                className={cn("min-h-11 rounded-lg border px-3 py-2 text-left text-sm", basis === b ? "border-accent bg-accent/10" : "border-border")}>
                {b === "self" ? "My own voice" : "Someone else's voice, with their permission"}
              </button>
            ))}
          </div>
          {basis === "permission" && (
            <label className="mt-3 block text-sm">
              <span className="mb-1 block font-medium">Speaker's name</span>
              <input className={field} value={speaker} onChange={(e) => setSpeaker(e.target.value)} maxLength={100} />
            </label>
          )}
          <label className="mt-4 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
            <span>{statement.data?.statement ?? "…"}</span>
          </label>
          <p className="mt-2 text-xs text-muted">This consent is kept with the voice. Everything Rasva makes with it is labelled synthetic.</p>
          <Button className="mt-4" disabled={!accepted || (basis === "permission" && !speaker.trim())} onClick={() => setStep(1)}>Continue</Button>
        </>
      )}

      {step === 1 && (
        <>
          <CardTitle>Record 10–15 seconds</CardTitle>
          <CardDescription>A quiet room, about a hand's width from the mic. Read this naturally:</CardDescription>
          <div role="tablist" aria-label="Language" className="mt-3 inline-flex rounded-lg border border-border bg-surface-2 p-1">
            {(["hi", "en"] as const).map((l) => (
              <button key={l} role="tab" aria-selected={language === l} onClick={() => setLanguage(l)}
                className={cn("min-h-9 rounded-md px-3 text-sm", language === l ? "bg-surface text-fg shadow-sm" : "text-muted")}>
                {l === "hi" ? "Hindi" : "English"}
              </button>
            ))}
          </div>
          <p className="mt-3 rounded-lg bg-surface-2 p-3 text-base leading-relaxed">{PROMPT[language]}</p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {recording ? (
              <Button variant="danger" onClick={() => stopRef.current()}><Square aria-hidden /> Stop ({seconds.toFixed(0)} s)</Button>
            ) : (
              <Button disabled={busy} onClick={() => void record()}><Mic aria-hidden /> {ref ? "Record again" : "Record"}</Button>
            )}
            <input ref={fileRef} type="file" accept="audio/*,.wav,.m4a,.mp3,.webm,.ogg,.flac" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f, f.name); e.target.value = ""; }} />
            {!recording && <Button variant="ghost" disabled={busy} onClick={() => fileRef.current?.click()}><Upload aria-hidden /> Upload a file</Button>}
          </div>
          {recording && (
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden>
              <div className={cn("h-full transition-[width] duration-75", level > 0.9 ? "bg-danger" : "bg-accent")}
                style={{ width: `${Math.min(100, Math.sqrt(level) * 180)}%` }} />
            </div>
          )}
          {busy && <p className="mt-3 text-sm text-muted">Checking the recording…</p>}
          {ref && <audio controls src={assetUrl(ref.asset.id)} className="mt-3 w-full" aria-label="Your recording" />}
          {ref && <QualityReport q={ref.quality} />}
          <div className="mt-4 flex gap-2">
            <Button variant="ghost" onClick={() => setStep(0)}>Back</Button>
            <Button disabled={!ref || ref.quality.level === "error"} onClick={() => setStep(2)}>Continue</Button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <CardTitle>What did you say?</CardTitle>
          <CardDescription>The transcript must match the recording word for word. Fix it if you changed anything.</CardDescription>
          <textarea className="mt-3 w-full rounded-lg border border-border bg-bg p-3 text-base" rows={4} value={transcript}
            onChange={(e) => setTranscript(e.target.value)} />
          <label className="mt-3 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            The transcript matches the recording exactly.
          </label>
          <div className="mt-4 flex gap-2">
            <Button variant="ghost" onClick={() => setStep(1)}>Back</Button>
            <Button disabled={!confirmed || !transcript.trim()} onClick={() => setStep(3)}>Continue</Button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <CardTitle>Name this voice</CardTitle>
          <label className="mt-3 block text-sm">
            <span className="mb-1 block font-medium">Name</span>
            <input className={field} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </label>
          <div className="mt-4 flex gap-2">
            <Button variant="ghost" onClick={() => setStep(2)}>Back</Button>
            <Button disabled={busy || !name.trim()} onClick={() => void save()}>Save voice</Button>
          </div>
        </>
      )}

      {error && <Notice tone="error" className="mt-3">{error}</Notice>}
      <Button variant="ghost" size="sm" className="mt-3" onClick={() => void cancelAll()}>Cancel</Button>
    </Card>
  );
}

export function VoicesPage() {
  const list = useAsync(voices.list);
  const log = useAsync(voices.consentLog);
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState<string>();
  const [error, setError] = useState<string>();
  const reload = async () => {
    await Promise.all([list.reload(), log.reload()]);
  };

  return (
    <>
      <PageHeader
        title="My voices"
        description="Narrate in your own voice, or the voice of someone who has given permission. Recordings stay on your Mac."
        actions={!adding && <Button onClick={() => setAdding(true)}><Mic aria-hidden /> Add a voice</Button>}
      />
      <Notice className="mb-4">
        Using your own voice needs a voice engine that supports it, which arrives in a later update. You can set up your
        voice now; it will be ready when that engine is installed.
      </Notice>
      {adding && <div className="mb-4"><NewVoice onDone={() => { setAdding(false); void reload(); }} /></div>}
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {list.data?.length === 0 && !adding && (
        <EmptyState icon={<Mic />} title="No voices yet" action={<Button onClick={() => setAdding(true)}>Add a voice</Button>}>
          Record about 10 seconds of clear speech. Rasva checks the recording and tells you how to improve it.
        </EmptyState>
      )}
      <ul className="space-y-3">
        {list.data?.map((v) => (
          <li key={v.id}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <CardTitle>{v.name}</CardTitle>
                <div className="flex gap-1">
                  <Badge>{v.language === "hi" ? "Hindi" : v.language === "en" ? "English" : "Any language"}</Badge>
                  <Badge tone={v.quality.level === "good" ? "success" : "warning"}>{v.quality.level === "good" ? "Clear recording" : "Usable recording"}</Badge>
                </div>
              </div>
              <CardDescription>
                {v.consent.basis === "self" ? "Your own voice" : `${v.consent.speaker_name}'s voice, with permission`} · added {timeAgo(v.created_at)} from {v.consent.recorded_from}
              </CardDescription>
              <audio controls preload="none" src={assetUrl(v.reference_asset_id)} className="mt-3 w-full" aria-label={`${v.name} recording`} />
              <div className="mt-3">
                {confirm === v.id ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    Delete this voice and its recording?
                    <Button size="sm" variant="danger" onClick={async () => {
                      setError(undefined);
                      try { await voices.remove(v.id); await reload(); } catch (e) { setError((e as Error).message); }
                      setConfirm(undefined);
                    }}>Delete</Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirm(undefined)}>Keep</Button>
                  </div>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => setConfirm(v.id)}><Trash2 aria-hidden /> Delete</Button>
                )}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {log.data && log.data.length > 0 && (
        <details className="mt-6 text-sm">
          <summary className="cursor-pointer text-muted">Consent history</summary>
          <ul className="mt-2 space-y-1 text-xs text-muted">
            {log.data.map((e) => (
              <li key={e.id}>{timeAgo(e.at)} · {e.event === "granted" ? "Added" : "Deleted"} “{e.profile_name}” ({e.basis === "self" ? "own voice" : `permission from ${e.speaker_name}`})</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
