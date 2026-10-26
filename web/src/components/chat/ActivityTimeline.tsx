"use client";

import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Code2,
} from "lucide-react";
import { useState } from "react";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { countSteps, type ActivityStep } from "@/lib/activity";
import { formatDuration } from "@/lib/time";

function StatusIcon({ status }: { status: ActivityStep["status"] }) {
  if (status === "running") return <Spinner className="h-3.5 w-3.5 text-accent" />;
  if (status === "error")
    return <AlertCircle aria-label="Failed" className="h-3.5 w-3.5 text-bad" />;
  if (status === "stopped")
    return <CircleSlash aria-label="Stopped" className="h-3.5 w-3.5 text-faint" />;
  return <CheckCircle2 aria-label="Done" className="h-3.5 w-3.5 text-ok" />;
}

function StepRow({ step }: { step: ActivityStep }) {
  const [showSql, setShowSql] = useState(false);
  return (
    <li className="relative pl-6">
      <span className="absolute top-[3px] left-0 flex h-4 w-4 items-center justify-center rounded-full bg-bg">
        <StatusIcon status={step.status} />
      </span>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">{step.title}</span>
        {step.detail && <span className="text-muted">{step.detail}</span>}
        {step.result && (
          <span
            className={cn("text-xs", step.status === "error" ? "text-bad" : "text-muted")}
          >
            {step.result}
          </span>
        )}
      </div>
      {step.todos.length > 0 && (
        <ul
          aria-label="Plan"
          className="mt-1 flex flex-col gap-0.5 text-[13px] text-muted"
        >
          {step.todos.map((todo, index) => (
            <li key={index} className="flex items-start gap-1.5">
              <span aria-hidden="true">
                {todo.status === "completed"
                  ? "✓"
                  : todo.status === "in_progress"
                    ? "›"
                    : "○"}
              </span>
              <span className={todo.status === "completed" ? "line-through" : undefined}>
                {todo.text}
              </span>
            </li>
          ))}
        </ul>
      )}
      {step.sql && (
        <div className="mt-1">
          <button
            type="button"
            aria-expanded={showSql}
            onClick={() => setShowSql((current) => !current)}
            className="inline-flex items-center gap-1 rounded text-xs text-muted hover:text-fg"
          >
            <Code2 aria-hidden="true" className="h-3.5 w-3.5" />
            {showSql ? "Hide SQL" : "Show SQL"}
          </button>
          {showSql && <CodeBlock code={step.sql} className="mt-1.5" />}
        </div>
      )}
      {step.children.length > 0 && <StepList steps={step.children} nested />}
    </li>
  );
}

function StepList({
  steps,
  nested = false,
}: {
  steps: ActivityStep[];
  nested?: boolean;
}) {
  return (
    <ol
      className={cn(
        "relative flex flex-col gap-2.5 border-l border-line",
        nested ? "mt-2 ml-2 pl-0" : "ml-2 pl-0",
      )}
    >
      {steps.map((step) => (
        <StepRow key={step.id} step={step} />
      ))}
    </ol>
  );
}

interface ActivityTimelineProps {
  steps: ActivityStep[];
  /** The agent is still working on this block. */
  isLive: boolean;
  durationMs?: number;
}

export function ActivityTimeline({ steps, isLive, durationMs }: ActivityTimelineProps) {
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? isLive;
  const total = countSteps(steps);
  const running = findRunning(steps);
  const summary = isLive
    ? `${running?.title ?? "Working"}…`
    : `${total} ${total === 1 ? "step" : "steps"}${durationMs ? ` · ${formatDuration(durationMs)}` : ""}`;

  return (
    <section
      aria-label="Agent activity"
      className="rc-fade-in rounded-card border border-line bg-surface"
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setOverride(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "h-4 w-4 text-muted transition-transform",
            expanded && "rotate-90",
          )}
        />
        {isLive ? (
          <Spinner className="h-3.5 w-3.5 text-accent" />
        ) : (
          <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5 text-ok" />
        )}
        <span className="font-medium">{summary}</span>
        {isLive && <span className="text-xs text-muted">{total} so far</span>}
      </button>
      {expanded && (
        <div className="border-t border-line px-3 py-3">
          <StepList steps={steps} />
        </div>
      )}
    </section>
  );
}

function findRunning(steps: ActivityStep[]): ActivityStep | undefined {
  for (const step of [...steps].reverse()) {
    const nested = findRunning(step.children);
    if (nested) return nested;
    if (step.status === "running") return step;
  }
  return undefined;
}
