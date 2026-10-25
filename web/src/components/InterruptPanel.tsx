import type { ApprovalRequest, DecisionType } from "@/lib/interrupt";

interface Props {
  request: ApprovalRequest;
  onDecision: (type: DecisionType) => void;
}

export function InterruptPanel({ request, onDecision }: Props) {
  return (
    <section
      aria-labelledby="approval-heading"
      className="rounded-lg border border-amber-400 bg-amber-50 p-3"
    >
      <h2 id="approval-heading" className="font-semibold">
        Approval required: {request.toolName}
      </h2>
      {request.description && (
        <p className="mt-1 whitespace-pre-wrap">{request.description}</p>
      )}
      {request.sql && (
        <pre
          aria-label="SQL to approve"
          className="mt-2 overflow-x-auto rounded bg-white p-2 font-mono text-sm"
        >
          <code>{request.sql}</code>
        </pre>
      )}
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => onDecision("approve")}
          className="rounded bg-green-600 px-3 py-1 text-white"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => onDecision("reject")}
          className="rounded bg-red-600 px-3 py-1 text-white"
        >
          Reject
        </button>
      </div>
    </section>
  );
}
