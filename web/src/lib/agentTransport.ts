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
 * `FetchStreamTransport`. `reset()` starts a new thread on the next run.
 */
export class AgentThreadTransport implements UseStreamTransport {
  private threadId: string | null = null;

  constructor(private readonly agentUrl: string) {}

  reset(): void {
    this.threadId = null;
  }

  async stream(payload: UseStreamTransportPayload) {
    const threadId = this.threadId ?? (await this.createThread(payload.signal));
    this.threadId = threadId;
    const apiUrl = `${this.agentUrl}/threads/${encodeURIComponent(threadId)}/runs/stream`;
    return new FetchStreamTransport({ apiUrl }).stream(payload);
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
    return body.thread_id;
  }
}
