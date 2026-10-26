import { cn } from "@/lib/cn";
import { CopyButton } from "./CopyButton";

interface CodeBlockProps {
  code: string;
  label?: string;
  className?: string;
}

/** Monospace block with a copy button, e.g. for SQL. */
export function CodeBlock({ code, label = "SQL", className }: CodeBlockProps) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-control border border-line bg-code",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-line px-3 py-0.5">
        <span className="text-[11px] font-medium tracking-wide text-muted uppercase">
          {label}
        </span>
        <CopyButton text={code} label={`Copy ${label}`} />
      </div>
      <pre
        aria-label={label}
        tabIndex={0}
        className="max-h-72 overflow-auto p-3 font-mono text-[12.5px] leading-relaxed"
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}
