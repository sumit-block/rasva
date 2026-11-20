import { Archive, FolderOpen, Plus, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";

export function LibraryPage() {
  const [query, setQuery] = useState("");
  const projects = useAsync(() => api.projects({ q: query }), [query]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const [creating, setCreating] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError(undefined);
    try {
      await api.createProject({ name: name.trim(), module: "audio" });
      setName("");
      await projects.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const archive = async (id: string) => {
    try {
      await api.updateProject(id, { status: "archived" });
      await projects.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <>
      <PageHeader title="Library" description="Projects, uploaded references and generated files." />
      <Card className="mb-4">
        <form onSubmit={create} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 text-sm">
            <span className="mb-1 block font-medium">New audio project</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              placeholder="e.g. महाभारत — Chapter 1"
              className="min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-fg placeholder:text-muted"
            />
          </label>
          <Button type="submit" disabled={creating || !name.trim()}>
            <Plus aria-hidden /> Create project
          </Button>
        </form>
        <p className="mt-2 text-xs text-muted">Image projects become available after the audio studio is released.</p>
      </Card>

      <label className="relative mb-4 block">
        <span className="sr-only">Search projects</span>
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name"
          className="min-h-11 w-full rounded-lg border border-border bg-surface pr-3 pl-9 text-fg placeholder:text-muted"
        />
      </label>

      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {projects.error && <Notice tone="error" className="mb-4">{projects.error.message}</Notice>}

      {projects.data && projects.data.length === 0 ? (
        <EmptyState icon={<FolderOpen />} title={query ? "No matching projects" : "No projects yet"}>
          {query ? "Try a different name." : "Create a project above to keep scripts, voices and results together."}
        </EmptyState>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data?.map((p) => (
            <li key={p.id}>
              <Card className="flex h-full flex-col">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="font-medium break-words">{p.name}</h2>
                  <Badge tone="accent">{p.module === "audio" ? "Audio" : "Image"}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">Updated {timeAgo(p.updated_at)}</p>
                <div className="mt-auto pt-4">
                  <Button size="sm" variant="ghost" onClick={() => archive(p.id)}>
                    <Archive aria-hidden /> Archive
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
