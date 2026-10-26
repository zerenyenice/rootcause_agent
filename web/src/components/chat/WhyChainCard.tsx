import {
  AlertTriangle,
  CheckCircle2,
  Radar,
  ShieldCheck,
  Target,
  XCircle,
  Zap,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { CodeBlock } from "@/components/ui/CodeBlock";
import type { CountermeasureType, WhyChain, WhyEvidence, WhyLevel } from "@/lib/whychain";
import { ConfidenceMeter } from "./ConfidenceMeter";

const COUNTERMEASURE_GROUPS: readonly {
  type: CountermeasureType;
  label: string;
  icon: ReactNode;
}[] = [
  { type: "immediate", label: "Immediate", icon: <Zap className="h-4 w-4 text-warn" /> },
  {
    type: "preventive",
    label: "Preventive",
    icon: <ShieldCheck className="h-4 w-4 text-ok" />,
  },
  {
    type: "detection",
    label: "Detection",
    icon: <Radar className="h-4 w-4 text-accent-text" />,
  },
];

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-muted uppercase">
      {children}
    </h3>
  );
}

function Evidence({ items }: { items: WhyEvidence[] }) {
  if (items.length === 0) return null;
  return (
    <details className="group mt-2">
      <summary className="cursor-pointer text-xs text-muted select-none hover:text-fg">
        Evidence ({items.length})
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        {items.map((item, index) => (
          <div key={index}>
            <CodeBlock code={item.sql} />
            {item.rowsSummary && (
              <p className="mt-1 text-xs text-muted">{item.rowsSummary}</p>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

function Chain({ levels }: { levels: WhyLevel[] }) {
  if (levels.length === 0) return null;
  return (
    <section aria-label="Why chain">
      <SectionTitle>Why chain</SectionTitle>
      <ol className="flex flex-col">
        {levels.map((level, index) => (
          <li key={index} className="relative flex gap-3 pb-5 last:pb-0">
            {index < levels.length - 1 && (
              <span
                aria-hidden="true"
                className="absolute top-7 bottom-0 left-[13px] w-px bg-line-strong"
              />
            )}
            <span
              aria-hidden="true"
              className="z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-xs font-semibold text-accent-text"
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <p className="font-semibold">
                  <span className="sr-only">{`Why ${index + 1}: `}</span>
                  {level.question}
                </p>
                <ConfidenceMeter value={level.confidence} />
              </div>
              <p className="mt-0.5 text-muted">{level.answer}</p>
              <Evidence items={level.evidence} />
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Problem({ chain }: { chain: WhyChain }) {
  const cells = [
    ["What", chain.problem.what],
    ["When", chain.problem.when],
    ["Where", chain.problem.where],
    ["Impact", chain.problem.impact],
  ] as const;
  return (
    <section aria-label="Problem statement">
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-control border border-line bg-line md:grid-cols-4">
        {cells.map(([label, value]) => (
          <div key={label} className="bg-surface p-3">
            <dt className="text-[11px] font-semibold tracking-wide text-muted uppercase">
              {label}
            </dt>
            <dd className="mt-0.5">{value || "—"}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function RootCause({ chain }: { chain: WhyChain }) {
  const root = chain.rootCause;
  if (!root) return null;
  return (
    <section
      aria-label="Root cause"
      className="rounded-card border border-accent/40 bg-accent/10 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Target aria-hidden="true" className="h-4 w-4 text-accent-text" />
        <h3 className="font-semibold text-accent-text">Root cause</h3>
        {root.type && <Badge tone="accent">{root.type}</Badge>}
        <span className="ml-auto">
          <ConfidenceMeter value={root.confidence} />
        </span>
      </div>
      <p className="mt-2 text-[15px] font-medium">{root.statement}</p>
    </section>
  );
}

function Countermeasures({ chain }: { chain: WhyChain }) {
  if (chain.countermeasures.length === 0) return null;
  return (
    <section aria-label="Countermeasures">
      <SectionTitle>Countermeasures</SectionTitle>
      <div className="grid gap-3 md:grid-cols-3">
        {COUNTERMEASURE_GROUPS.map((group) => {
          const items = chain.countermeasures.filter((c) => c.type === group.type);
          if (items.length === 0) return null;
          return (
            <div key={group.type} className="rounded-control border border-line p-3">
              <div className="mb-1.5 flex items-center gap-1.5 font-medium">
                {group.icon}
                {group.label}
              </div>
              <ul className="flex flex-col gap-1.5">
                {items.map((item, index) => (
                  <li key={index}>
                    {item.action}
                    {item.owner && (
                      <span className="block text-xs text-muted">
                        Owner: {item.owner}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Validation({ chain }: { chain: WhyChain }) {
  if (chain.validation.length === 0) return null;
  return (
    <section aria-label="Validation checks">
      <SectionTitle>Validation</SectionTitle>
      <ul className="flex flex-col gap-1.5">
        {chain.validation.map((check, index) => (
          <li key={index} className="flex items-start gap-2">
            {check.passed ? (
              <CheckCircle2
                aria-label="Passed"
                className="mt-0.5 h-4 w-4 shrink-0 text-ok"
              />
            ) : (
              <XCircle aria-label="Failed" className="mt-0.5 h-4 w-4 shrink-0 text-bad" />
            )}
            <span>{check.link}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const CLAMP_MIN_CHARS = 200;

/** Long text clamped to three lines with a Show more / Show less toggle. */
function ClampedText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > CLAMP_MIN_CHARS;
  return (
    <>
      <p className={isLong && !expanded ? "line-clamp-3" : undefined}>{text}</p>
      {isLong && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
          className="mt-1 text-xs font-medium underline underline-offset-2"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </>
  );
}

function ChainBody({ chain }: { chain: WhyChain }) {
  return (
    <div className="flex flex-col gap-5">
      {chain.partial && (
        <Banner tone="warn" title="Partial analysis" alert={false}>
          <ClampedText
            text={
              chain.notes ||
              "The investigation stopped before reaching a confirmed root cause."
            }
          />
        </Banner>
      )}
      {!chain.partial && chain.notes && <p className="text-muted">{chain.notes}</p>}
      <Problem chain={chain} />
      <Chain levels={chain.levels} />
      <RootCause chain={chain} />
      <Countermeasures chain={chain} />
      <Validation chain={chain} />
      {chain.branches.map((branch, index) => (
        <details key={index} className="rounded-control border border-line p-3">
          <summary className="flex cursor-pointer items-center gap-2 font-medium select-none">
            <AlertTriangle aria-hidden="true" className="h-4 w-4 text-muted" />
            Alternative branch {index + 1}
            {branch.problem.what && (
              <span className="truncate font-normal text-muted">
                {branch.problem.what}
              </span>
            )}
          </summary>
          <div className="mt-3">
            <ChainBody chain={branch} />
          </div>
        </details>
      ))}
    </div>
  );
}

/** The hero of the product: a 5-Whys analysis rendered as a connected chain. */
export function WhyChainCard({ chain }: { chain: WhyChain }) {
  return (
    <article
      aria-label="Root cause analysis"
      className="rc-fade-in rounded-card border border-line bg-surface p-5 shadow-xs"
    >
      <ChainBody chain={chain} />
    </article>
  );
}
