import { Compass } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/notice";

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
