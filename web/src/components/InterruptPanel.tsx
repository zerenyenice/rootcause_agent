import { useEffect, useRef } from "react";
import type { ApprovalRequest, DecisionType } from "@/lib/interrupt";

interface Props {
  requests: ApprovalRequest[];
  onDecision: (type: DecisionType) => void;
}

// One decision per request is sent, so approve/reject apply to the whole batch
// and every statement in it is shown below.
export function InterruptPanel({ requests, onDecision }: Props) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const isBatch = requests.length > 1;

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      aria-labelledby="approval-heading"
      className="rounded-lg border border-amber-400 bg-amber-50 p-3"
    >
      <h2 id="approval-heading" ref={headingRef} tabIndex={-1} className="font-semibold">
        {isBatch
          ? `Approval required: ${requests.length} queries`
          : `Approval required: ${requests[0]?.toolName ?? "tool"}`}
      </h2>
      <ol className="mt-1 space-y-2">
        {requests.map((request, index) => (
          <li key={index}>
            {request.description && (
              <p className="whitespace-pre-wrap">{request.description}</p>
            )}
            {request.sql && (
              <pre
                aria-label={`SQL to approve${isBatch ? ` (${index + 1})` : ""}`}
                className="mt-1 overflow-x-auto rounded bg-white p-2 font-mono text-sm"
              >
                <code>{request.sql}</code>
              </pre>
            )}
          </li>
        ))}
      </ol>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => onDecision("approve")}
          className="rounded bg-green-600 px-3 py-1 text-white"
        >
          {isBatch ? "Approve all" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() => onDecision("reject")}
          className="rounded bg-red-600 px-3 py-1 text-white"
        >
          {isBatch ? "Reject all" : "Reject"}
        </button>
      </div>
    </section>
  );
}
