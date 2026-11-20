import { ListChecks } from "lucide-react";
import { JobRow } from "@/components/JobRow";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useJobs } from "@/hooks/useJobs";

export function JobsPage() {
  const { jobs, connected } = useJobs();
  return (
    <>
      <PageHeader title="Jobs" description="One generation runs at a time so your Mac stays responsive. Others wait their turn." />
      {!connected && (
        <Notice tone="error" className="mb-4">
          Can't reach Rasva on your Mac. Saved work is safe there. Reconnecting automatically…
        </Notice>
      )}
      {jobs.length === 0 ? (
        <EmptyState icon={<ListChecks />} title="No jobs yet">
          When you generate something it shows up here with live progress. You can cancel or retry from this page.
        </EmptyState>
      ) : (
        <ul className="space-y-3" aria-live="polite">
          {jobs.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
        </ul>
      )}
    </>
  );
}
