"use client";

import { ShieldAlert } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/Button";
import { CodeBlock } from "@/components/ui/CodeBlock";
import type { ApprovalRequest, DecisionType } from "@/lib/interrupt";

interface ApprovalCardProps {
  requests: ApprovalRequest[];
  onDecision: (type: DecisionType) => void;
}

// One decision per request is sent, so approve/reject apply to the whole batch
// and every statement in it is shown below.
export function ApprovalCard({ requests, onDecision }: ApprovalCardProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const isBatch = requests.length > 1;

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      aria-labelledby="approval-heading"
      className="rc-fade-in rounded-card border border-warn/40 bg-warn/5 p-4"
    >
      <div className="flex items-center gap-2">
        <ShieldAlert aria-hidden="true" className="h-4 w-4 text-warn" />
        <h2
          id="approval-heading"
          ref={headingRef}
          tabIndex={-1}
          className="font-semibold focus:outline-none"
        >
          {isBatch
            ? `Approval needed to run ${requests.length} queries`
            : "Approval needed to run this query"}
        </h2>
      </div>
      <ol className="mt-3 flex flex-col gap-3">
        {requests.map((request, index) => (
          <li key={index}>
            {request.description && (
              <p className="mb-1.5 whitespace-pre-wrap text-muted">
                {request.description}
              </p>
            )}
            {request.sql && (
              <CodeBlock
                code={request.sql}
                label={`SQL to approve${isBatch ? ` (${index + 1})` : ""}`}
              />
            )}
          </li>
        ))}
      </ol>
      <div className="mt-4 flex gap-2">
        <Button variant="primary" onClick={() => onDecision("approve")}>
          {isBatch ? "Approve all" : "Approve"}
        </Button>
        <Button variant="secondary" onClick={() => onDecision("reject")}>
          {isBatch ? "Reject all" : "Reject"}
        </Button>
      </div>
    </section>
  );
}
