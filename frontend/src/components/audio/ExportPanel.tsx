import { Download, FileAudio } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import { api, assetUrl, chapters, TERMINAL_STATES, type Asset, type Chapter, type Job } from "@/lib/api";
import { formatBytes, formatDuration, STATE_LABEL, timeAgo } from "@/lib/format";

function ExportRow({ job }: { job: Job }) {
  const [files, setFiles] = useState<Asset[]>([]);
  useEffect(() => {
    if (job.state !== "completed") return;
    Promise.all(job.outputs.map((id) => api.asset(id))).then(setFiles, () => setFiles([]));
  }, [job.state, job.outputs]);
  const running = !TERMINAL_STATES.has(job.state);
  const main = files.find((f) => f.original_name === "chapter.wav");
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>{timeAgo(job.created_at)}</span>
        <span className="text-xs text-muted">
          {job.state === "completed" && typeof job.metrics.audio_s === "number" ? formatDuration(job.metrics.audio_s) : STATE_LABEL[job.state]}
        </span>
      </div>
      {running && <Progress className="mt-2" value={job.state === "processing" ? job.progress : null} label="Exporting" />}
      {running && job.stage_detail && <p className="mt-1 text-xs text-muted">{job.stage_detail}</p>}
      {job.state === "failed" && <p className="mt-1 text-sm text-danger">{job.error_message}</p>}
      {main && <audio controls preload="none" src={assetUrl(main.id)} className="mt-2 w-full" aria-label="Exported chapter" />}
      {files.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {files.map((f) => (
            <a key={f.id} href={assetUrl(f.id, true)} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-surface-2">
              <Download className="size-4" aria-hidden /> {f.original_name} <span className="text-xs text-muted">{formatBytes(f.size_bytes)}</span>
            </a>
          ))}
        </div>
      )}
    </li>
  );
}

export function ExportPanel({ chapter, onExported }: { chapter: Chapter; onExported: () => void }) {
  const { prefs } = usePrefs();
  const { jobs, upsert } = useJobs();
  const [mp3, setMp3] = useState(false);
  const [stems, setStems] = useState(false);
  const [error, setError] = useState<string>();
  const missing = chapter.segments.filter((s) => !s.current).length;

  // Prefer live job state from the event stream over the chapter snapshot.
  const exportsLive = chapter.exports.map((e) => jobs.find((j) => j.id === e.id) ?? e);

  const go = async () => {
    setError(undefined);
    try {
      upsert(await chapters.export(chapter.id, { formats: mp3 ? ["wav", "mp3"] : ["wav"], stems }));
      onExported();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card>
      <CardTitle>Export</CardTitle>
      <CardDescription>
        Joins every sentence with natural pauses{chapter.tracks.length ? ", mixes your music and effects," : ""} and levels the
        volume so nothing clips.
      </CardDescription>
      <div className="mt-3 space-y-2 text-sm">
        <label className="flex min-h-11 items-center gap-2">
          <input type="checkbox" checked={mp3} disabled={!chapter.mp3_available} onChange={(e) => setMp3(e.target.checked)} />
          Also make an MP3
          {!chapter.mp3_available && <span className="text-xs text-muted">(needs FFmpeg on this Mac)</span>}
        </label>
        {prefs.mode === "advanced" && chapter.tracks.length > 0 && (
          <label className="flex min-h-11 items-center gap-2">
            <input type="checkbox" checked={stems} onChange={(e) => setStems(e.target.checked)} />
            Separate files for narration, music and effects
          </label>
        )}
      </div>
      {missing > 0 && (
        <Notice className="mt-3">{missing} sentences don't have audio yet. Generate them first.</Notice>
      )}
      {error && <Notice tone="error" className="mt-3">{error}</Notice>}
      <Button className="mt-3" disabled={!chapter.summary.exportable} onClick={() => void go()}>
        <FileAudio aria-hidden /> Export chapter
      </Button>
      {exportsLive.length > 0 && <ul className="mt-3 divide-y divide-border">{exportsLive.map((j) => <ExportRow key={j.id} job={j} />)}</ul>}
    </Card>
  );
}
