import { Smartphone, Trash2, Wifi, WifiOff } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { usePrefs } from "@/hooks/usePrefs";
import { lan, type LanStatus } from "@/lib/api";
import { timeAgo } from "@/lib/format";

function PairingPanel({ status, onDone }: { status: LanStatus; onDone: () => void }) {
  const [qr, setQr] = useState("");
  const [left, setLeft] = useState(0);
  const pairing = status.pairing!;
  const address = status.addresses[0] ?? status.hostname;
  const setupUrl = `http://${address}:${status.setup_port}/#code=${pairing.code}`;

  useEffect(() => {
    QRCode.toDataURL(setupUrl, { margin: 1, width: 352, errorCorrectionLevel: "M" }).then(setQr, () => setQr(""));
  }, [setupUrl]);
  useEffect(() => {
    const tick = () => setLeft(Math.max(0, Math.round(pairing.expires_at - Date.now() / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [pairing.expires_at]);

  return (
    <div className="mt-4 rounded-lg border border-accent/40 bg-accent/5 p-4">
      <div className="flex flex-col gap-4 sm:flex-row">
        {qr && (
          <img src={qr} alt="QR code to connect a device" className="size-44 shrink-0 self-center rounded-lg bg-white p-2" />
        )}
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          <li>Scan this with the phone's camera (same Wi-Fi as this Mac).</li>
          <li>Install the Rasva certificate it offers, then trust it.</li>
          <li>Tap “Open Rasva Studio”. If asked, enter this code:</li>
        </ol>
      </div>
      <p className="mt-3 text-center text-3xl font-semibold tracking-[0.3em] tabular-nums" aria-label={`Pairing code ${pairing.code.split("").join(" ")}`}>
        {pairing.code}
      </p>
      <p className="text-center text-xs text-muted">
        {left > 0 ? `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "Expired"}
      </p>
      <p className="mt-3 text-xs text-muted">
        Or open <span className="font-mono">{setupUrl.split("#")[0]}</span> on the device.
      </p>
      {status.ca_fingerprint && (
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer">Certificate fingerprint (to compare on the phone)</summary>
          <p className="mt-1 break-all font-mono">{status.ca_fingerprint}</p>
        </details>
      )}
      <Button variant="ghost" size="sm" className="mt-2" onClick={onDone}>Done</Button>
    </div>
  );
}

export function DevicesCard() {
  const { prefs } = usePrefs();
  const status = useAsync(lan.status);
  const devices = useAsync(lan.devices);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [confirm, setConfirm] = useState<string>();
  const known = useRef<number | null>(null);
  const [justPaired, setJustPaired] = useState<string>();

  const pairing = status.data?.pairing;
  // While a code is showing, watch for the new device so the Mac confirms it.
  useEffect(() => {
    if (!pairing) return;
    known.current = devices.data?.length ?? 0;
    const t = window.setInterval(async () => {
      const list = await lan.devices().catch(() => null);
      if (list && known.current !== null && list.length > known.current) {
        devices.setData(list);
        setJustPaired(list[0]?.name);
        status.setData(await lan.stopPairing());
      }
    }, 2000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairing?.code]);

  if (prefs.client !== "local") {
    return (
      <Card>
        <CardTitle>This device</CardTitle>
        <CardDescription>
          Connected to your Mac over home Wi-Fi as “{prefs.deviceName ?? "a paired device"}”. Your Mac does all the
          work and keeps all files. Pairing and storage settings can only be changed on the Mac.
        </CardDescription>
      </Card>
    );
  }

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const s = status.data;

  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <CardTitle>Phone &amp; other devices</CardTitle>
        {s && <Badge tone={s.running ? "success" : "neutral"}>{s.running ? "On" : "Off"}</Badge>}
      </div>
      <CardDescription>
        Start and listen to jobs from your phone or another laptop on the same Wi-Fi. Everything still runs and stays on
        this Mac. Only devices you pair can connect, and the connection is encrypted.
      </CardDescription>

      {s && (
        <div className="mt-4 flex flex-wrap gap-2">
          {s.running ? (
            <>
              {!s.pairing && (
                <Button disabled={busy} onClick={() => act(async () => { setJustPaired(undefined); status.setData(await lan.startPairing()); })}>
                  <Smartphone aria-hidden /> Pair a device
                </Button>
              )}
              <Button variant="ghost" disabled={busy} onClick={() => act(async () => status.setData(await lan.disable()))}>
                <WifiOff aria-hidden /> Turn off
              </Button>
            </>
          ) : (
            <Button disabled={busy} onClick={() => act(async () => status.setData(await lan.enable()))}>
              <Wifi aria-hidden /> Allow devices on my Wi-Fi
            </Button>
          )}
        </div>
      )}
      {s?.running && (
        <p className="mt-2 text-xs text-muted">
          Keep this Mac awake and Rasva running. Closing the lid usually stops access. macOS may ask once whether to allow
          incoming connections: choose Allow.
        </p>
      )}
      {justPaired && <Notice className="mt-3">“{justPaired}” is paired and connected.</Notice>}
      {s?.running && s.pairing && <PairingPanel status={s} onDone={() => void act(async () => status.setData(await lan.stopPairing()))} />}
      {(error || s?.error) && <Notice tone="error" className="mt-3">{error ?? s?.error}</Notice>}

      {devices.data && devices.data.length > 0 && (
        <>
          <h3 className="mt-5 text-sm font-medium">Paired devices</h3>
          <ul className="mt-2 divide-y divide-border">
            {devices.data.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span>
                  {d.name}
                  <span className="block text-xs text-muted">
                    Paired {timeAgo(d.created_at)}{d.last_seen_at ? ` · last used ${timeAgo(d.last_seen_at)}` : ""}
                  </span>
                </span>
                {confirm === d.id ? (
                  <span className="flex gap-2">
                    <Button size="sm" variant="danger" onClick={() => act(async () => { await lan.revoke(d.id); await devices.reload(); setConfirm(undefined); })}>
                      Remove
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirm(undefined)}>Keep</Button>
                  </span>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => setConfirm(d.id)} aria-label={`Remove ${d.name}`}>
                    <Trash2 aria-hidden /> Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
