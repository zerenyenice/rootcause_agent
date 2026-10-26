import type { Message } from "@langchain/langgraph-sdk";
import { buildSegments, buildSteps, countSteps } from "@/lib/activity";

const chain = {
  problem: { what: "w", when: "n", where: "h", impact: "i" },
  levels: [{ question: "Why?", answer: "Because", evidence: [], confidence: 0.5 }],
  root_cause: null,
};

function ai(
  id: string,
  calls: { id: string; name: string; args?: object }[],
  content = "",
) {
  return {
    type: "ai",
    id,
    content,
    tool_calls: calls.map((c) => ({ args: {}, ...c })),
  } as unknown as Message;
}

function tool(callId: string, content: unknown, extra: object = {}): Message {
  return {
    type: "tool",
    id: `t-${callId}`,
    tool_call_id: callId,
    content: typeof content === "string" ? content : JSON.stringify(content),
    ...extra,
  } as unknown as Message;
}

const human: Message = { type: "human", id: "h1", content: "why?" };

describe("buildSteps", () => {
  it("maps tool calls to friendly steps with result summaries", () => {
    const steps = buildSteps(
      [
        ai("a1", [
          { id: "1", name: "list_tables" },
          { id: "2", name: "describe_table", args: { table: "runs" } },
          { id: "3", name: "search_catalog", args: { query: "scrap" } },
          { id: "4", name: "run_sql", args: { sql: "SELECT 1", purpose: "Check scrap" } },
          { id: "5", name: "explain_sql", args: { sql: "SELECT 2" } },
          { id: "6", name: "write_todos" },
          { id: "7", name: "read_file" },
          { id: "8", name: "column_values", args: { table: "runs", column: "line" } },
        ]),
        tool("1", { tables: [1, 2] }),
        tool("2", { columns: [1, 2, 3] }),
        tool("3", { results: [1] }),
        tool("4", { row_count: 12, truncated: true }),
        tool("5", { estimated_rows: 12345 }),
        tool("6", "ok"),
        tool("7", "text"),
        tool("8", { values: ["a"] }),
      ],
      { isRunning: false },
    );
    expect(steps.map((s) => [s.title, s.detail, s.result])).toEqual([
      ["Exploring schema", null, "2 tables"],
      ["Exploring schema", "runs", "3 columns"],
      ["Searching catalog", "scrap", "1 match"],
      ["Running query", "Check scrap", "12 rows (truncated)"],
      ["Estimating query cost", null, "~12,345 rows estimated"],
      ["Planning", null, null],
      ["Read file", null, null],
      ["Exploring schema", "runs.line", "1 value"],
    ]);
    expect(steps[3]?.sql).toBe("SELECT 1");
    expect(steps.every((s) => s.status === "done")).toBe(true);
  });

  it("marks unanswered calls running or stopped and failures as errors", () => {
    const messages = [
      ai("a1", [
        { id: "1", name: "run_sql", args: { sql: "x" } },
        { id: "2", name: "run_sql", args: { sql: "y" } },
        { id: "3", name: "describe_table" },
      ]),
      tool("1", { error: { code: "QueryRejected", message: "Not allowed" } }),
      tool("3", "boom", { status: "error" }),
    ];
    const live = buildSteps(messages, { isRunning: true });
    expect(live.map((s) => s.status)).toEqual(["error", "running", "error"]);
    expect(live[0]?.result).toBe("Not allowed");
    expect(buildSteps(messages, { isRunning: false })[1]?.status).toBe("stopped");
  });

  it("nests subagent steps under the task call", () => {
    const sub = [ai("s1", [{ id: "x", name: "run_sql", args: { sql: "SELECT 1" } }])];
    const steps = buildSteps(
      [ai("a1", [{ id: "task1", name: "task", args: { description: "Investigate" } }])],
      { isRunning: true, subagentMessages: (id) => (id === "task1" ? sub : undefined) },
    );
    expect(steps[0]?.kind).toBe("investigate");
    expect(steps[0]?.children[0]?.title).toBe("Running query");
    expect(countSteps(steps)).toBe(2);
  });

  it("hides the WhyChain output tool and tolerates malformed calls", () => {
    const odd = {
      type: "ai",
      id: "z",
      content: "",
      tool_calls: [{ name: 5 }, null],
    } as unknown as Message;
    expect(
      buildSteps([ai("a", [{ id: "1", name: "WhyChain" }]), odd], { isRunning: false }),
    ).toEqual([]);
  });
});

describe("buildSegments", () => {
  it("orders user, activity, why chain and answer", () => {
    const segments = buildSegments(
      [
        human,
        ai("a1", [
          { id: "1", name: "run_sql", args: { sql: "SELECT 1" } },
          { id: "2", name: "task", args: { description: "d" } },
        ]),
        tool("1", { row_count: 1 }),
        tool("2", chain),
        ai("a2", [], "Summary text"),
      ],
      { isRunning: false },
    );
    expect(segments.map((s) => s.kind)).toEqual([
      "human",
      "activity",
      "whychain",
      "text",
    ]);
    const activity = segments[1];
    expect(activity?.kind === "activity" && activity.steps).toHaveLength(2);
    expect(activity?.kind === "activity" && activity.turn).toBe(1);
  });

  it("falls back to prose when the task result is not a chain", () => {
    const segments = buildSegments(
      [
        human,
        ai("a1", [{ id: "2", name: "task" }]),
        tool("2", "Plain text result"),
        ai("a2", [], "Done"),
      ],
      { isRunning: false },
    );
    expect(segments.map((s) => s.kind)).toEqual(["human", "activity", "text"]);
  });

  it("counts turns across several questions", () => {
    const segments = buildSegments(
      [
        human,
        ai("a1", [{ id: "1", name: "list_tables" }]),
        tool("1", { tables: [] }),
        { type: "human", id: "h2", content: "again" },
        ai("a2", [{ id: "2", name: "list_tables" }]),
      ],
      { isRunning: true },
    );
    const turns = segments.flatMap((s) => (s.kind === "activity" ? [s.turn] : []));
    expect(turns).toEqual([1, 2]);
  });
});
