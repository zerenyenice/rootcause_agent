import {
  FetchStreamTransport,
  type UseStreamTransport,
  type UseStreamTransportPayload,
} from "@langchain/langgraph-sdk/ui";

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
 */
export class AgentThreadTransport implements UseStreamTransport {
  private threadId: string | null = null;

  constructor(
    private readonly agentUrl: string,
    private readonly onThreadCreated?: (threadId: string) => void,
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
    return new FetchStreamTransport({ apiUrl }).stream(body as UseStreamTransportPayload);
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
