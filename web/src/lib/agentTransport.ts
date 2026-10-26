import type { Message } from "@langchain/langgraph-sdk";
import {
  FetchStreamTransport,
  type UseStreamTransport,
  type UseStreamTransportPayload,
} from "@langchain/langgraph-sdk/ui";

/** A raw SSE event as the SDK transport yields it. */
interface StreamEvent {
  id?: string;
  event: string;
  data: unknown;
}

/** Called with the full message list of a subagent run (keyed by namespace id). */
export type SubagentValuesListener = (namespace: string, messages: Message[]) => void;

interface ThreadCreated {
  thread_id: string;
}

function isThreadCreated(value: unknown): value is ThreadCreated {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { thread_id?: unknown }).thread_id === "string"
  );
}

/**
 * `useStream` transport for the agent service.
 *
 * `useStream` with a custom transport never creates threads on the server, so
 * the first run creates one with `POST /threads` and later runs reuse it; each
 * run streams from `/threads/{id}/runs/stream` through the SDK's
 * `FetchStreamTransport`. `reset()` starts a new thread on the next run and
 * `use(id)` continues an existing one. Every run asks for subgraph events so
 * subagent activity streams live (`stream_subgraphs`; `streamSubgraphs` is the
 * SDK's spelling of the same flag).
 *
 * Subagent events arrive namespaced (`values|tools:<pregel task id>`). That id is
 * not the `task` tool call id and the SDK's own mapping does not hold up, so this
 * transport takes those events out of the stream: it reports each subagent
 * `values` snapshot (its full message list) to `onSubagentValues` and drops the
 * rest (token chunks), so they never reach, or pollute, the main thread.
 */
export class AgentThreadTransport implements UseStreamTransport {
  private threadId: string | null = null;

  constructor(
    private readonly agentUrl: string,
    private readonly onThreadCreated?: (threadId: string) => void,
    private readonly onSubagentValues?: SubagentValuesListener,
  ) {}

  get currentThreadId(): string | null {
    return this.threadId;
  }

  reset(): void {
    this.threadId = null;
  }

  use(threadId: string): void {
    this.threadId = threadId;
  }

  async stream(payload: UseStreamTransportPayload) {
    const threadId = this.threadId ?? (await this.createThread(payload.signal));
    this.threadId = threadId;
    const apiUrl = `${this.agentUrl}/threads/${encodeURIComponent(threadId)}/runs/stream`;
    const body = { ...payload, streamSubgraphs: true, stream_subgraphs: true };
    const events = await new FetchStreamTransport({ apiUrl }).stream(
      body as UseStreamTransportPayload,
    );
    return this.withoutSubagentEvents(events);
  }

  private async *withoutSubagentEvents(
    events: AsyncGenerator<StreamEvent>,
  ): AsyncGenerator<StreamEvent> {
    for await (const item of events) {
      const namespace = subagentNamespace(item.event);
      if (namespace === null) {
        yield item;
        continue;
      }
      if (item.event.startsWith("values|")) {
        const messages = messagesOf(item.data);
        if (messages) this.onSubagentValues?.(namespace, messages);
      }
    }
  }

  private async createThread(signal: AbortSignal): Promise<string> {
    const response = await fetch(`${this.agentUrl}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal,
    });
    if (!response.ok) throw new Error(`Failed to create thread: ${response.status}`);
    const body: unknown = await response.json();
    if (!isThreadCreated(body)) throw new Error("Unexpected thread response");
    this.onThreadCreated?.(body.thread_id);
    return body.thread_id;
  }
}

const TOOLS_NAMESPACE = /^tools:[^|]+$/;

/** `values|tools:abc` -> "tools:abc"; null for events outside a subagent namespace. */
export function subagentNamespace(event: string): string | null {
  const separator = event.indexOf("|");
  if (separator < 0) return null;
  const namespace = event.slice(separator + 1);
  return TOOLS_NAMESPACE.test(namespace) ? namespace : null;
}

function messagesOf(data: unknown): Message[] | null {
  if (typeof data !== "object" || data === null) return null;
  const messages = (data as { messages?: unknown }).messages;
  return Array.isArray(messages) ? (messages as Message[]) : null;
}
