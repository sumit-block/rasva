import { Boxes } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { api, type EngineInfo } from "@/lib/api";
import { formatBytes } from "@/lib/format";

const LICENSE: Record<EngineInfo["license_class"], { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  permissive: { label: "Open source", tone: "success" },
  "open-weight": { label: "Open weights", tone: "warning" },
  restricted: { label: "Restricted licence", tone: "danger" },
  unknown: { label: "Licence unverified", tone: "danger" },
};

const INSTALL: Record<EngineInfo["install_state"], string> = {
  built_in: "Built in",
  not_installed: "Not installed",
  installing: "Installing",
  installed: "Installed",
  failed: "Install failed",
  removing: "Removing",
};

export function ModelsPage() {
  const models = useAsync(api.models);
  return (
    <>
      <PageHeader title="Models" description="Engines available on this Mac, with their licence and space needs." />
      {models.error && <Notice tone="error">{models.error.message}</Notice>}
      {models.data?.length === 0 && (
        <EmptyState icon={<Boxes />} title="No engines yet">Engines appear here once they are added.</EmptyState>
      )}
      <ul className="grid gap-3 md:grid-cols-2">
        {models.data?.map((m) => (
          <li key={m.id}>
            <Card className="h-full">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <CardTitle>{m.display_name}</CardTitle>
                <div className="flex gap-2">
                  <Badge tone={LICENSE[m.license_class].tone} title={m.license}>
                    {LICENSE[m.license_class].label}
                  </Badge>
                  <Badge>{INSTALL[m.install_state]}</Badge>
                </div>
              </div>
              <CardDescription>{m.summary}</CardDescription>
              <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                <dt className="text-muted">Licence</dt>
                <dd>{m.license}</dd>
                <dt className="text-muted">Download</dt>
                <dd>{m.approx_download_bytes ? formatBytes(m.approx_download_bytes) : "None"}</dd>
                {m.languages.length > 0 && (
                  <>
                    <dt className="text-muted">Languages</dt>
                    <dd>{m.languages.join(", ")}</dd>
                  </>
                )}
              </dl>
              {!m.compatibility.compatible && (
                <Notice tone="error" className="mt-3">{m.compatibility.reasons.join(" ")}</Notice>
              )}
            </Card>
          </li>
        ))}
      </ul>
    </>
  );
}
