import type { Message } from "@langchain/langgraph-sdk";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "@/components/chat/Chat";
import { EXAMPLE_QUESTIONS } from "@/components/chat/EmptyChat";
import { duckdbConnection, postgresConnection } from "./fixtures/admin";

const spies = vi.hoisted(() => ({
  use: vi.fn(),
  refresh: vi.fn().mockResolvedValue(undefined),
  setActiveThreadId: vi.fn(),
  loadThreadMessages: vi.fn(),
}));

vi.mock("@/lib/agentTransport", () => ({
  AgentThreadTransport: class {
    use = spies.use;
  },
}));
vi.mock("@/components/shell/ThreadsProvider", () => ({
  useThreads: () => ({
    refresh: spies.refresh,
    setActiveThreadId: spies.setActiveThreadId,
  }),
}));
vi.mock("@/lib/threads", () => ({ loadThreadMessages: spies.loadThreadMessages }));

interface FakeStream {
  messages: Message[];
  isLoading: boolean;
  error: unknown;
  interrupt: unknown;
  subagents: Map<string, unknown>;
  getSubagent: (id: string) => { messages: Message[] } | undefined;
  submit: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
}

const state = vi.hoisted(() => ({ current: undefined as unknown }));

vi.mock("@langchain/langgraph-sdk/react", () => ({ useStream: () => state.current }));

function setStream(overrides: Partial<FakeStream> = {}): FakeStream {
  const stream: FakeStream = {
    messages: [],
    isLoading: false,
    error: undefined,
    interrupt: undefined,
    subagents: new Map(),
    getSubagent: () => undefined,
    submit: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  state.current = stream;
  return stream;
}

const CONNECTIONS = [postgresConnection, duckdbConnection];

function renderChat(props: Partial<React.ComponentProps<typeof Chat>> = {}) {
  const onConnectionChange = vi.fn();
  const view = render(
    <Chat
      connections={CONNECTIONS}
      connection={postgresConnection}
      initialThreadId={null}
      onConnectionChange={onConnectionChange}
      {...props}
    />,
  );
  return { ...view, onConnectionChange };
}

const approvalInterrupt = {
  value: {
    action_requests: [
      {
        name: "run_sql",
        args: { sql: "SELECT * FROM t", token: "LEAKME" },
        description: "Big query, est. 5 GB",
      },
    ],
  },
};

const whyChain = {
  problem: { what: "Scrap up", when: "January", where: "Line 2", impact: "320 units" },
  levels: [
    {
      question: "Why did scrap rise?",
      answer: "A bad material lot",
      evidence: [{ sql: "SELECT scrap FROM runs", rows_summary: "12 rows" }],
      confidence: 0.9,
    },
  ],
  root_cause: { statement: "Sampling plan too small", type: "process", confidence: 0.7 },
  validation: [{ link: "Lot caused scrap", check_sql: "SELECT 1", passed: true }],
  countermeasures: [
    { type: "immediate", action: "Quarantine the lot", owner: "QA" },
    { type: "detection", action: "Alert on scrap spikes", owner: null },
  ],
  partial: true,
  notes: "Budget exhausted.",
};

const investigation: Message[] = [
  { type: "human", id: "h1", content: "Investigate scrap please" },
  {
    type: "ai",
    id: "a1",
    content: "",
    tool_calls: [
      { id: "c1", name: "run_sql", args: { sql: "SELECT 1", purpose: "Scrap by line" } },
      { id: "c2", name: "task", args: { description: "Investigate scrap" } },
    ],
  } as unknown as Message,
  {
    type: "tool",
    id: "t1",
    tool_call_id: "c1",
    content: JSON.stringify({ row_count: 4 }),
  } as unknown as Message,
  {
    type: "tool",
    id: "t2",
    tool_call_id: "c2",
    content: JSON.stringify(whyChain),
  } as unknown as Message,
  { type: "ai", id: "a2", content: "Scrap rose on **line 2**." },
];

beforeEach(() => {
  vi.clearAllMocks();
  spies.refresh.mockResolvedValue(undefined);
});

describe("Chat composer", () => {
  it("sends on Enter with the connection context and refocuses the input", async () => {
    const stream = setStream();
    renderChat();
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "hello{Enter}");
    expect(stream.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: "hello" }] },
      { context: { connection_id: postgresConnection.id } },
    );
    expect(input).toHaveFocus();
    expect(input).toHaveValue("");
  });

  it("adds a newline on Shift+Enter without sending", async () => {
    const stream = setStream();
    renderChat();
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "a{Shift>}{Enter}{/Shift}b");
    expect(stream.submit).not.toHaveBeenCalled();
    expect(input).toHaveValue("a\nb");
  });

  it("keeps Send disabled for blank input and sends via the button", async () => {
    const stream = setStream();
    renderChat();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(stream.submit).toHaveBeenCalledTimes(1);
  });

  it("turns Send into Stop while the agent runs", async () => {
    const stream = setStream({
      isLoading: true,
      messages: [investigation[0] as Message],
    });
    renderChat();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(stream.stop).toHaveBeenCalled();
  });
});

describe("Chat empty state", () => {
  it("shows the connection name and sends a clicked example question", async () => {
    const stream = setStream();
    renderChat();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getAllByText(postgresConnection.name)).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: EXAMPLE_QUESTIONS[0] }));
    expect(stream.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: EXAMPLE_QUESTIONS[0] }] },
      { context: { connection_id: postgresConnection.id } },
    );
  });

  it("lets the user switch connection from the pill", async () => {
    setStream();
    const { onConnectionChange } = renderChat();
    await userEvent.click(screen.getByRole("button", { name: /Connection: Warehouse/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /Local demo/ }));
    expect(onConnectionChange).toHaveBeenCalledWith(duckdbConnection.id);
  });
});

describe("Chat conversation", () => {
  it("renders the activity summary, the WhyChain card and the answer", async () => {
    setStream({ messages: investigation });
    renderChat();

    expect(screen.getByText("Investigate scrap please")).toBeInTheDocument();
    const activity = screen.getByRole("region", { name: "Agent activity" });
    expect(within(activity).getByText("2 steps")).toBeInTheDocument();

    await userEvent.click(within(activity).getByRole("button", { name: /2 steps/ }));
    expect(within(activity).getByText("Running query")).toBeInTheDocument();
    expect(within(activity).getByText("4 rows")).toBeInTheDocument();
    await userEvent.click(within(activity).getByRole("button", { name: "Show SQL" }));
    expect(within(activity).getByText("SELECT 1")).toBeInTheDocument();

    const card = screen.getByRole("article", { name: "Root cause analysis" });
    expect(within(card).getByText("Partial analysis")).toBeInTheDocument();
    expect(within(card).getByText("Budget exhausted.")).toBeInTheDocument();
    expect(within(card).getByText("Scrap up")).toBeInTheDocument();
    expect(within(card).getByText("Why did scrap rise?")).toBeInTheDocument();
    const meters = within(card).getAllByRole("meter", { name: "Confidence" });
    expect(meters.map((m) => m.getAttribute("aria-valuenow"))).toEqual(["90", "70"]);
    expect(within(card).getByText("Sampling plan too small")).toBeInTheDocument();
    expect(within(card).getByText("process")).toBeInTheDocument();
    expect(within(card).getByText("Quarantine the lot")).toBeInTheDocument();
    expect(within(card).getByText("Alert on scrap spikes")).toBeInTheDocument();
    expect(within(card).getByLabelText("Passed")).toBeInTheDocument();
    expect(screen.getByText("line 2", { selector: "strong" })).toBeInTheDocument();
  });

  it("shows nested subagent steps live under the task", () => {
    const sub: Message[] = [
      {
        type: "ai",
        id: "s1",
        content: "",
        tool_calls: [{ id: "x1", name: "run_sql", args: { sql: "SELECT 2" } }],
      } as unknown as Message,
    ];
    setStream({
      isLoading: true,
      messages: investigation.slice(0, 2),
      getSubagent: (id) => (id === "c2" ? { messages: sub } : undefined),
    });
    renderChat();
    const activity = screen.getByRole("region", { name: "Agent activity" });
    expect(within(activity).getByText("Investigating root cause")).toBeInTheDocument();
    expect(within(activity).getAllByText("Running query").length).toBeGreaterThan(0);
  });

  it("falls back to markdown when the task result is not a WhyChain", () => {
    const messages = investigation.map((m) =>
      m.id === "t2" ? ({ ...m, content: "Plain summary" } as Message) : m,
    );
    setStream({ messages });
    renderChat();
    expect(screen.queryByRole("article")).toBeNull();
  });
});

describe("Chat approvals", () => {
  it("shows the SQL and resumes with approve or reject", async () => {
    const stream = setStream({ isLoading: false, interrupt: approvalInterrupt });
    renderChat();

    expect(screen.getByText("Approval needed to run this query")).toBeInTheDocument();
    expect(screen.getByText("SELECT * FROM t")).toBeInTheDocument();
    expect(screen.getByText("Big query, est. 5 GB")).toBeInTheDocument();
    expect(screen.queryByText(/LEAKME/)).toBeNull();
    expect(screen.getByLabelText("Message")).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(stream.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "approve" }] } },
      context: { connection_id: postgresConnection.id },
    });

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(stream.submit).toHaveBeenLastCalledWith(null, {
      command: { resume: { decisions: [{ type: "reject" }] } },
      context: { connection_id: postgresConnection.id },
    });
  });

  it("sends one decision per request for a batch", async () => {
    const stream = setStream({
      interrupt: {
        value: {
          action_requests: [
            { name: "run_sql", args: { sql: "SELECT 1" }, description: "" },
            { name: "run_sql", args: { sql: "SELECT 2" }, description: "" },
          ],
        },
      },
    });
    renderChat();
    await userEvent.click(screen.getByRole("button", { name: "Approve all" }));
    expect(stream.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "approve" }, { type: "approve" }] } },
      context: { connection_id: postgresConnection.id },
    });
  });
});

describe("Chat errors and history", () => {
  it("shows a friendly banner with Retry and never the raw error", async () => {
    const stream = setStream({
      error: new Error("Traceback: secret stack"),
      messages: [{ type: "human", id: "h1", content: "retry me" }],
    });
    renderChat();
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("Something went wrong");
    expect(banner).not.toHaveTextContent("secret");
    await userEvent.click(within(banner).getByRole("button", { name: "Retry" }));
    expect(stream.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: "retry me" }] },
      { context: { connection_id: postgresConnection.id } },
    );
  });

  it("loads a past thread and points the transport at it", async () => {
    setStream();
    spies.loadThreadMessages.mockResolvedValue(investigation);
    renderChat({ initialThreadId: "thread-1" });
    expect(
      await screen.findByRole("article", { name: "Root cause analysis" }),
    ).toBeVisible();
    expect(spies.use).toHaveBeenCalledWith("thread-1");
    expect(spies.loadThreadMessages).toHaveBeenCalledWith("thread-1");
  });

  it("explains when a past thread cannot be loaded", async () => {
    setStream();
    spies.loadThreadMessages.mockResolvedValue(null);
    renderChat({ initialThreadId: "gone" });
    expect(
      await screen.findByText("Could not load this conversation"),
    ).toBeInTheDocument();
  });

  it("refreshes the thread list when a run finishes", async () => {
    setStream({ isLoading: true, messages: [investigation[0] as Message] });
    const { rerender } = renderChat();
    setStream({ isLoading: false, messages: investigation });
    rerender(
      <Chat
        connections={CONNECTIONS}
        connection={postgresConnection}
        initialThreadId={null}
        onConnectionChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(spies.refresh).toHaveBeenCalled());
  });
});
