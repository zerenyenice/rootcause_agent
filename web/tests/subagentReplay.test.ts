import type { Message } from "@langchain/langgraph-sdk";
import { buildSegments } from "@/lib/activity";
import { AgentThreadTransport, subagentNamespace } from "@/lib/agentTransport";
import { resolveSubagents, type SubagentRuns } from "@/lib/subagents";
import slice from "./fixtures/real-stream-slice.json";

// Trimmed slice of a REAL agent-service run: the orchestrator's `task` call plus the
// namespaced events (`values|tools:<pregel task id>`, ...) of the subagent it started.
type RecordedEvent = [string, unknown];
const RECORDED = slice as RecordedEvent[];

const sdk = vi.hoisted(() => ({ events: [] as { event: string; data: unknown }[] }));

vi.mock("@langchain/langgraph-sdk/ui", () => ({
  FetchStreamTransport: class {
    async stream() {
      return (async function* () {
        for (const item of sdk.events) yield item;
      })();
    }
  },
}));

async function replay() {
  sdk.events = RECORDED.map(([event, data]) => ({ event, data }));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ thread_id: "t" }))),
  );
  const runs: Record<string, Message[]> = {};
  const transport = new AgentThreadTransport(
    "http://agent",
    undefined,
    (ns, messages) => {
      runs[ns] = messages;
    },
  );
  const stream = await transport.stream({
    input: {},
    signal: new AbortController().signal,
  } as never);
  const forwarded: string[] = [];
  const mainMessages: Message[] = [];
  for await (const item of stream) {
    forwarded.push(item.event);
    if (item.event === "updates") {
      const update = item.data as Record<string, { messages?: Message[] }>;
      for (const node of Object.values(update))
        mainMessages.push(...(node.messages ?? []));
    }
  }
  return { runs: runs as SubagentRuns, forwarded, mainMessages };
}

afterEach(() => vi.unstubAllGlobals());

describe("replaying a real subagent stream", () => {
  it("recognises subagent namespaces", () => {
    expect(subagentNamespace("values|tools:abc")).toBe("tools:abc");
    expect(subagentNamespace("values")).toBeNull();
    expect(subagentNamespace("values|model:abc")).toBeNull();
    expect(subagentNamespace("messages|tools:a|tools:b")).toBeNull();
  });

  it("keeps namespaced events out of the main stream and reports subagent snapshots", async () => {
    const { runs, forwarded } = await replay();
    expect(forwarded).toEqual(["metadata", "updates"]);
    const namespaces = Object.keys(runs);
    expect(namespaces).toHaveLength(1);
    expect(namespaces[0]).toMatch(/^tools:[0-9a-f-]{36}$/);
    // The latest snapshot wins: more messages than the first one in the slice.
    expect(runs[namespaces[0] as string]?.length).toBeGreaterThan(5);
  });

  it("nests the subagent's steps under the task call, though the ids differ", async () => {
    const { runs, mainMessages } = await replay();
    const taskCall = mainMessages
      .flatMap(
        (m) => (m as { tool_calls?: { id: string; name: string }[] }).tool_calls ?? [],
      )
      .find((c) => c.name === "task");
    expect(taskCall?.id).toMatch(/^call_/);
    expect(Object.keys(runs)[0]).not.toContain(taskCall?.id);

    const resolved = resolveSubagents(mainMessages, runs);
    expect(resolved.get(taskCall?.id ?? "")?.length).toBeGreaterThan(5);

    const segments = buildSegments(mainMessages, {
      isRunning: true,
      subagentMessages: (id) => resolved.get(id),
    });
    const activity = segments.find((s) => s.kind === "activity");
    const task = activity?.kind === "activity" ? activity.steps[0] : undefined;
    expect(task?.title).toBe("Investigating root cause");
    const titles = task?.children.map((s) => s.title) ?? [];
    expect(titles).toContain("Exploring schema");
    expect(titles).toContain("Searching catalog");
    expect(task?.children.find((s) => s.title === "Searching catalog")?.result).toBe(
      "0 matches",
    );
  });

  it("pairs runs with task calls by order when the brief does not match", () => {
    const call = {
      type: "ai",
      id: "a",
      content: "",
      tool_calls: [{ id: "c1", name: "task", args: { description: "something else" } }],
    } as unknown as Message;
    const run: Message[] = [{ type: "human", id: "h", content: "different brief" }];
    const resolved = resolveSubagents([call], { "tools:n1": run });
    expect(resolved.get("c1")).toBe(run);
    expect(resolveSubagents([call], {}).size).toBe(0);
  });
});
