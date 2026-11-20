import { Smartphone } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { lan } from "@/lib/api";

function guessName(): string {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && "ontouchend" in document)) return "iPad";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android phone" : "Android tablet";
  if (/Windows/.test(ua)) return "Windows laptop";
  if (/Macintosh/.test(ua)) return "Mac";
  return "My device";
}

/** Shown on a phone or laptop that reached the Wi-Fi port but isn't paired yet. */
export function PairPage() {
  const fromLink = /code=(\d{6})/.exec(window.location.hash)?.[1] ?? "";
  const [code, setCode] = useState(fromLink);
  const [name, setName] = useState(guessName());
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await lan.pair(code, name);
      // Drop the code from history and open the app.
      window.location.replace("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full bg-bg px-4 py-10">
      <form onSubmit={submit} className="mx-auto max-w-sm">
        <div className="mb-6 flex items-center gap-2 font-semibold">
          <img src="/favicon.svg" alt="" className="size-6" /> Rasva Studio
        </div>
        <div className="mb-4 inline-flex rounded-full bg-accent/12 p-3 text-accent"><Smartphone className="size-6" aria-hidden /></div>
        <h1 className="text-2xl font-semibold tracking-tight">Pair this device</h1>
        <p className="mt-2 text-sm text-muted">
          On your Mac, open Settings → Phone &amp; other devices → Pair a device, then enter the 6-digit code shown there.
        </p>
        <label className="mt-6 block text-sm">
          <span className="mb-1 block font-medium">Code</span>
          <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric" autoComplete="one-time-code" required pattern="\d{6}"
            className="min-h-12 w-full rounded-lg border border-border bg-surface px-3 text-center text-2xl tracking-[0.3em] tabular-nums" />
        </label>
        <label className="mt-4 block text-sm">
          <span className="mb-1 block font-medium">Name this device</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required
            className="min-h-11 w-full rounded-lg border border-border bg-surface px-3" />
          <span className="mt-1 block text-xs text-muted">You'll see this name on your Mac and can remove it there.</span>
        </label>
        {error && <Notice tone="error" className="mt-4">{error}</Notice>}
        <Button type="submit" className="mt-6 w-full" disabled={busy || code.length !== 6}>Pair</Button>
      </form>
    </div>
  );
}
