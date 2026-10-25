// Human-in-the-loop (run_sql approval) contract, isolated in one module.
//
// Contract (LangChain HumanInTheLoopMiddleware via DeepAgents `interrupt_on`):
//   interrupt value: { action_requests: [{ name, args, description }], review_configs: [...] }
//   resume value:    { decisions: [{ type: "approve" | "reject" }] }
// The estimate is carried in `description`. The approver needs to see the SQL,
// so `args.sql` is the single raw arg that is surfaced; nothing else from args.

export type DecisionType = "approve" | "reject";

export interface ResumePayload {
  decisions: { type: DecisionType }[];
}

export interface ApprovalRequest {
  toolName: string;
  description: string;
  sql: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseApprovalRequest(value: unknown): ApprovalRequest | null {
  if (!isRecord(value) || !Array.isArray(value.action_requests)) return null;
  const first: unknown = value.action_requests[0];
  if (!isRecord(first)) return null;
  const args = isRecord(first.args) ? first.args : {};
  return {
    toolName: typeof first.name === "string" ? first.name : "tool",
    description: typeof first.description === "string" ? first.description : "",
    sql: typeof args.sql === "string" ? args.sql : null,
  };
}

export function buildResume(type: DecisionType): ResumePayload {
  return { decisions: [{ type }] };
}
