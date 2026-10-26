import { cn } from "@/lib/cn";

/** Small confidence bar plus percentage; renders nothing when unknown. */
export function ConfidenceMeter({ value }: { value: number | null }) {
  if (value === null) return null;
  const percent = Math.round(value * 100);
  return (
    <span
      className="inline-flex items-center gap-2 text-xs text-muted"
      title={`Confidence ${percent}%`}
    >
      <span
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1.5 w-14 overflow-hidden rounded-full bg-subtle"
      >
        <span
          className={cn(
            "block h-full rounded-full",
            percent < 50 ? "bg-warn" : "bg-accent",
          )}
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="tabular-nums">{percent}%</span>
    </span>
  );
}
