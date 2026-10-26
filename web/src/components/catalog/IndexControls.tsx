import { Hammer } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import type { IndexStatus } from "@/lib/api";

interface Chip {
  tone: BadgeTone;
  text: string;
}

export function indexChip(index: IndexStatus | null, isStale: boolean): Chip {
  if (index === null) return { tone: "neutral", text: "Index status unknown" };
  switch (index.status) {
    case "idle":
      return { tone: "neutral", text: "Not built" };
    case "running":
      return { tone: "accent", text: "Building…" };
    case "failed":
      return { tone: "bad", text: "Build failed" };
    case "done":
      return isStale
        ? { tone: "warn", text: "Stale" }
        : { tone: "ok", text: "Up to date" };
  }
}

interface IndexControlsProps {
  index: IndexStatus | null;
  isStale: boolean;
  isStarting: boolean;
  onBuild: () => void;
}

export function IndexControls({
  index,
  isStale,
  isStarting,
  onBuild,
}: IndexControlsProps) {
  const chip = indexChip(index, isStale);
  const isRunning = index?.status === "running";
  const detail =
    index && index.status === "done"
      ? `${index.indexed_tables} tables, ${index.indexed_columns} columns indexed`
      : undefined;
  return (
    <div className="flex items-center gap-2">
      <span role="status" aria-label="Index status" title={detail}>
        <Badge
          tone={chip.tone}
          icon={isRunning ? <Spinner className="h-3 w-3" /> : undefined}
        >
          {chip.text}
        </Badge>
      </span>
      <Button
        variant="primary"
        size="sm"
        icon={<Hammer className="h-3.5 w-3.5" />}
        disabled={isRunning || isStarting}
        loading={isStarting}
        onClick={onBuild}
      >
        Build index
      </Button>
    </div>
  );
}
