import { AudioLines, Compass, FlaskConical, Image as ImageIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/notice";

export function AudioStudioPage() {
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Audio Studio" />
      <EmptyState
        icon={<AudioLines />}
        title="Narration arrives with the first voice engine"
        action={
          <Button variant="secondary" onClick={() => navigate("/")}>
            Go to the system test
          </Button>
        }
      >
        You'll write or paste a script, choose a voice, preview a sentence and generate the full narration here.
      </EmptyState>
    </>
  );
}

export function ImageStudioPage() {
  return (
    <>
      <PageHeader title="Image Studio" />
      <EmptyState icon={<ImageIcon />} title="Coming after the audio studio">
        Pose and outfit changes using your own authorised reference photos will be added once narration is complete
        and tested.
      </EmptyState>
    </>
  );
}

export function ExperimentsPage() {
  return (
    <>
      <PageHeader title="Experiments" />
      <EmptyState icon={<FlaskConical />} title="No experiments yet">
        Side-by-side engine comparisons on fixed inputs, with timing, memory and your ratings, will appear here.
      </EmptyState>
    </>
  );
}

export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <EmptyState
      icon={<Compass />}
      title="Page not found"
      action={
        <Button variant="secondary" onClick={() => navigate("/")}>
          Go home
        </Button>
      }
    >
      That page doesn't exist.
    </EmptyState>
  );
}
