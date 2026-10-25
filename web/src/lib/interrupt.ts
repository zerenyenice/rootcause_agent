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

/**
 * Parse every action request, in order. HumanInTheLoopMiddleware needs exactly
 * one decision per request, so malformed items keep their slot (with defaults)
 * rather than being dropped. Returns null when there is nothing to approve.
 */
export function parseApprovalRequests(value: unknown): ApprovalRequest[] | null {
  if (!isRecord(value) || !Array.isArray(value.action_requests)) return null;
  const items: unknown[] = value.action_requests;
  if (items.length === 0) return null;
  return items.map((item) => {
    const request = isRecord(item) ? item : {};
    const args = isRecord(request.args) ? request.args : {};
    return {
      toolName: typeof request.name === "string" ? request.name : "tool",
      description: typeof request.description === "string" ? request.description : "",
      sql: typeof args.sql === "string" ? args.sql : null,
    };
  });
}

/** One decision per action request, in order. */
export function buildResume(type: DecisionType, count = 1): ResumePayload {
  return { decisions: Array.from({ length: count }, () => ({ type })) };
}
