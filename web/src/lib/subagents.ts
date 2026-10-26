import type { Message } from "@langchain/langgraph-sdk";
import { messageText } from "./messages";

/** Subagent message lists by namespace id, in order of first appearance. */
export type SubagentRuns = Readonly<Record<string, readonly Message[]>>;

interface TaskCall {
  id: string;
  description: string;
}

function taskCalls(messages: readonly Message[]): TaskCall[] {
  return messages.flatMap((message) => {
    if (message.type !== "ai") return [];
    const calls: unknown = (message as { tool_calls?: unknown }).tool_calls;
    if (!Array.isArray(calls)) return [];
    return calls.flatMap((call): TaskCall[] => {
      if (typeof call !== "object" || call === null) return [];
      const { id, name, args } = call as { id?: unknown; name?: unknown; args?: unknown };
      if (name !== "task" || typeof id !== "string") return [];
      const description = (args as { description?: unknown } | null)?.description;
      return [{ id, description: typeof description === "string" ? description : "" }];
    });
  });
}

function briefOf(run: readonly Message[]): string {
  const first = run.find((m) => m.type === "human");
  return first ? messageText(first).trim() : "";
}

/**
 * Pair each `task` tool call with the subagent run it started. The run's namespace
 * is a graph task id, not the tool call id, so a run is matched by its brief (the
 * first human message equals the call's `description`); runs that don't match are
 * paired with the remaining calls in order of appearance.
 */
export function resolveSubagents(
  messages: readonly Message[],
  runs: SubagentRuns,
): Map<string, readonly Message[]> {
  const calls = taskCalls(messages);
  const namespaces = Object.keys(runs);
  const resolved = new Map<string, readonly Message[]>();
  const used = new Set<string>();

  for (const call of calls) {
    const match = namespaces.find(
      (ns) =>
        !used.has(ns) &&
        call.description !== "" &&
        briefOf(runs[ns] ?? []) === call.description.trim(),
    );
    if (match) {
      used.add(match);
      resolved.set(call.id, runs[match] ?? []);
    }
  }
  const spare = namespaces.filter((ns) => !used.has(ns));
  for (const call of calls) {
    if (resolved.has(call.id)) continue;
    const ns = spare.shift();
    if (ns === undefined) break;
    resolved.set(call.id, runs[ns] ?? []);
  }
  return resolved;
}
