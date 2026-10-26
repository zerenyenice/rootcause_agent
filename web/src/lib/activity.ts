import type { Message } from "@langchain/langgraph-sdk";
import { messageText } from "./messages";
import { parseWhyChain, type WhyChain } from "./whychain";

export type StepKind = "schema" | "catalog" | "query" | "investigate" | "plan" | "other";
export type StepStatus = "running" | "done" | "error" | "stopped";

export interface ActivityStep {
  id: string;
  kind: StepKind;
  title: string;
  /** One-line context: table name, search text, purpose of a query. */
  detail: string | null;
  sql: string | null;
  /** One-line result: "12 rows", "3 columns", or a friendly error. */
  result: string | null;
  status: StepStatus;
  children: ActivityStep[];
}

export type Segment =
  | { kind: "human"; key: string; message: Message; turn: number }
  | { kind: "text"; key: string; message: Message }
  | { kind: "activity"; key: string; steps: ActivityStep[]; turn: number }
  | { kind: "whychain"; key: string; chain: WhyChain };

type Rec = Record<string, unknown>;

interface ToolCallLike {
  id: string;
  name: string;
  args: Rec;
}

const TASK_TOOL = "task";
const WHY_CHAIN_TOOL = "WhyChain";
const MAX_DETAIL_CHARS = 140;
const SCHEMA_TOOLS = new Set(["list_tables", "describe_table", "column_values"]);

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function truncate(text: string): string {
  return text.length > MAX_DETAIL_CHARS
    ? `${text.slice(0, MAX_DETAIL_CHARS - 1)}…`
    : text;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function toolCallsOf(message: Message): ToolCallLike[] {
  if (message.type !== "ai") return [];
  const calls: unknown = (message as { tool_calls?: unknown }).tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls.flatMap((call, index): ToolCallLike[] => {
    if (!isRecord(call) || typeof call.name !== "string") return [];
    const id =
      typeof call.id === "string" && call.id ? call.id : `${message.id ?? "m"}-${index}`;
    return [{ id, name: call.name, args: isRecord(call.args) ? call.args : {} }];
  });
}

function parsePayload(content: string): unknown {
  try {
    return JSON.parse(content);
  } catch {
    return null;
  }
}

function errorText(payload: unknown): string | null {
  if (!isRecord(payload) || !("error" in payload)) return null;
  const error = payload.error;
  if (typeof error === "string") return error;
  if (isRecord(error) && typeof error.message === "string") return error.message;
  return "The step failed.";
}

function arrayLength(value: unknown): number | null {
  return Array.isArray(value) ? value.length : null;
}

function summarize(name: string, payload: unknown): string | null {
  if (!isRecord(payload)) return null;
  if (name === "run_sql") {
    const count = typeof payload.row_count === "number" ? payload.row_count : null;
    if (count === null) return null;
    return `${plural(count, "row")}${payload.truncated === true ? " (truncated)" : ""}`;
  }
  if (name === "list_tables") {
    const n = arrayLength(payload.tables);
    return n === null ? null : plural(n, "table");
  }
  if (name === "describe_table") {
    const n = arrayLength(payload.columns);
    return n === null ? null : plural(n, "column");
  }
  if (name === "column_values") {
    const n = arrayLength(payload.values);
    return n === null ? null : plural(n, "value");
  }
  if (name === "explain_sql" && typeof payload.estimated_rows === "number") {
    return `~${Math.round(payload.estimated_rows).toLocaleString("en-US")} rows estimated`;
  }
  const n = arrayLength(payload.results) ?? arrayLength(payload.matches);
  return n === null ? null : plural(n, "match");
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function describeCall(
  call: ToolCallLike,
): Pick<ActivityStep, "kind" | "title" | "detail" | "sql"> {
  const { name, args } = call;
  if (SCHEMA_TOOLS.has(name)) {
    const table = str(args.table);
    const column = str(args.column);
    return {
      kind: "schema",
      title: "Exploring schema",
      detail: table ? (column ? `${table}.${column}` : table) : null,
      sql: null,
    };
  }
  if (name === "search_catalog") {
    return {
      kind: "catalog",
      title: "Searching catalog",
      detail: str(args.query) ?? str(args.q),
      sql: null,
    };
  }
  if (name === "run_sql" || name === "explain_sql") {
    const purpose = str(args.purpose);
    return {
      kind: "query",
      title: name === "run_sql" ? "Running query" : "Estimating query cost",
      detail: purpose ? truncate(purpose) : null,
      sql: str(args.sql),
    };
  }
  if (name === TASK_TOOL) {
    const description = str(args.description);
    return {
      kind: "investigate",
      title: "Investigating root cause",
      detail: description ? truncate(description) : null,
      sql: null,
    };
  }
  if (name === "write_todos") {
    return { kind: "plan", title: "Planning", detail: null, sql: null };
  }
  const readable = name.replace(/[_-]+/g, " ").trim();
  return {
    kind: "other",
    title: readable ? readable.charAt(0).toUpperCase() + readable.slice(1) : "Working",
    detail: null,
    sql: null,
  };
}

function toolMessagesById(messages: readonly Message[]): Map<string, Message> {
  const byId = new Map<string, Message>();
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id)
      byId.set(message.tool_call_id, message);
  }
  return byId;
}

export interface BuildOptions {
  /** True while the agent is still working (unanswered calls are "running"). */
  isRunning: boolean;
  /** Messages the subagent behind a `task` call has streamed so far. */
  subagentMessages?: (toolCallId: string) => readonly Message[] | undefined;
}

function buildStep(
  call: ToolCallLike,
  results: Map<string, Message>,
  options: BuildOptions,
): ActivityStep {
  const base = describeCall(call);
  const result = results.get(call.id);
  const nested =
    call.name === TASK_TOOL ? options.subagentMessages?.(call.id) : undefined;
  const children = nested ? buildSteps(nested, options) : [];
  if (!result) {
    return {
      id: call.id,
      ...base,
      result: null,
      status: options.isRunning ? "running" : "stopped",
      children,
    };
  }
  const content = messageText(result);
  const payload = parsePayload(content);
  const failure =
    errorText(payload) ??
    (result.type === "tool" && result.status === "error" ? "The step failed." : null);
  return {
    id: call.id,
    ...base,
    result: failure ?? (call.name === TASK_TOOL ? null : summarize(call.name, payload)),
    status: failure ? "error" : "done",
    children,
  };
}

/** Steps for every tool call in `messages`, in call order. */
export function buildSteps(
  messages: readonly Message[],
  options: BuildOptions,
): ActivityStep[] {
  const results = toolMessagesById(messages);
  return messages.flatMap((message) =>
    toolCallsOf(message)
      .filter((call) => call.name !== WHY_CHAIN_TOOL)
      .map((call) => buildStep(call, results, options)),
  );
}

/** Total steps including nested subagent steps. */
export function countSteps(steps: readonly ActivityStep[]): number {
  return steps.reduce((sum, step) => sum + 1 + countSteps(step.children), 0);
}

/**
 * Split a conversation into renderable segments: user bubbles, assistant prose,
 * activity timelines (consecutive tool calls merged) and WhyChain results.
 */
export function buildSegments(
  messages: readonly Message[],
  options: BuildOptions,
): Segment[] {
  const results = toolMessagesById(messages);
  const segments: Segment[] = [];
  let pending: ActivityStep[] = [];
  let turn = 0;

  const flush = (key: string) => {
    if (pending.length > 0) {
      segments.push({ kind: "activity", key: `activity-${key}`, steps: pending, turn });
      pending = [];
    }
  };

  messages.forEach((message, index) => {
    const key = message.id ?? String(index);
    if (message.type === "human") {
      flush(key);
      turn += 1;
      segments.push({ kind: "human", key, message, turn });
      return;
    }
    if (message.type !== "ai") return;
    for (const call of toolCallsOf(message)) {
      if (call.name === WHY_CHAIN_TOOL) continue;
      pending.push(buildStep(call, results, options));
      const result = results.get(call.id);
      const chain =
        call.name === TASK_TOOL && result ? parseWhyChain(messageText(result)) : null;
      if (chain) {
        flush(call.id);
        segments.push({ kind: "whychain", key: `chain-${call.id}`, chain });
      }
    }
    if (messageText(message).trim()) {
      flush(key);
      segments.push({ kind: "text", key, message });
    }
  });
  flush("tail");
  return segments;
}
