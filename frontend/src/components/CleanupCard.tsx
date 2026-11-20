import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { cleanup } from "@/lib/api";
import { formatBytes } from "@/lib/format";

/** Owner-only storage cleanup (PRD §23). Your uploads and the audio chapters use are never listed. */
export function CleanupCard({ onCleaned }: { onCleaned: () => void }) {
  const plan = useAsync(cleanup.plan);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "info" | "error"; text: string }>();

  useEffect(() => {
    if (plan.data) setSelected(new Set(plan.data.groups.filter((g) => g.default && g.count > 0).map((g) => g.id)));
  }, [plan.data]);

  const groups = plan.data?.groups.filter((g) => g.count > 0) ?? [];
  const chosen = groups.filter((g) => selected.has(g.id));
  const total = chosen.reduce((n, g) => n + g.bytes, 0);

  const run = async () => {
    setBusy(true);
    setMessage(undefined);
    try {
      const result = await cleanup.run(chosen.map((g) => g.id));
      setMessage({ tone: "info", text: `Freed ${formatBytes(result.freed_bytes)}.` });
      plan.setData(result);
      onCleaned();
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <Card className="lg:col-span-2">
      <CardTitle>Free up space</CardTitle>
      <CardDescription>
        Your uploads, the audio your chapters use, versions waiting for your review and the latest export of each chapter
        are never listed here.
      </CardDescription>
      {plan.error && <Notice tone="error" className="mt-3">{plan.error.message}</Notice>}
      {plan.data && groups.length === 0 && <p className="mt-3 text-sm text-muted">Nothing to clean up right now.</p>}
      {groups.length > 0 && (
        <ul className="mt-3 divide-y divide-border">
          {groups.map((g) => (
            <li key={g.id}>
              <label className="flex min-h-14 cursor-pointer items-start gap-3 py-2">
                <input type="checkbox" className="mt-1" checked={selected.has(g.id)}
                  onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(g.id); else n.delete(g.id); return n; })} />
                <span className="flex-1">
                  <span className="flex justify-between gap-2 text-sm font-medium">
                    {g.label} <span className="text-muted tabular-nums">{formatBytes(g.bytes)}</span>
                  </span>
                  <span className="block text-xs text-muted">{g.description} ({g.count} {g.count === 1 ? "item" : "items"})</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {plan.data && plan.data.missing_files > 0 && (
        <Notice className="mt-3">
          {plan.data.missing_files} files Rasva knows about are missing from disk. They may have been moved or deleted
          outside the app.
        </Notice>
      )}
      {message && <Notice tone={message.tone} className="mt-3">{message.text}</Notice>}
      {chosen.length > 0 && !confirming && (
        <Button className="mt-3" variant="secondary" onClick={() => setConfirming(true)}>
          <Sparkles aria-hidden /> Clean up {formatBytes(total)}
        </Button>
      )}
      {confirming && (
        <Notice className="mt-3">
          <p>Delete {chosen.map((g) => g.label.toLowerCase()).join(", ")} ({formatBytes(total)})? This can't be undone.</p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant="danger" disabled={busy} onClick={() => void run()}>Delete</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </Notice>
      )}
    </Card>
  );
}
