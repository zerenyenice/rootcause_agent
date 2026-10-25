import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "@/components/Chat";
import { ChatPage } from "@/components/ChatPage";
import { duckdbConnection, postgresConnection } from "./fixtures/admin";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      public readonly code: string,
      message: string,
      public readonly status = 400,
    ) {
      super(message);
      this.name = "ApiError";
    }
  }
  return { ApiError, listConnections: vi.fn() };
});

vi.mock("@/lib/api", () => api);

vi.mock("@/lib/agentTransport", () => ({
  AgentThreadTransport: class {
    reset = vi.fn();
  },
}));

const stream = vi.hoisted(() => ({
  current: {
    messages: [] as unknown[],
    isLoading: false,
    error: undefined as unknown,
    interrupt: undefined as { value: unknown } | undefined,
    submit: vi.fn(),
    stop: vi.fn(),
    switchThread: vi.fn(),
  },
}));

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: () => stream.current,
}));

const approvalInterrupt = {
  value: {
    action_requests: [{ name: "run_sql", args: { sql: "SELECT 1" }, description: "q" }],
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  stream.current = {
    messages: [],
    isLoading: false,
    error: undefined,
    interrupt: undefined,
    submit: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    switchThread: vi.fn(),
  };
  api.listConnections.mockResolvedValue([postgresConnection, duckdbConnection]);
});

async function send(text: string) {
  await userEvent.type(screen.getByLabelText("Message"), `${text}{Enter}`);
}

describe("Chat with a connection", () => {
  it("sends the connection id as run context", async () => {
    render(<Chat connectionId="conn-1" />);

    await send("why did revenue drop?");

    expect(stream.current.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: "why did revenue drop?" }] },
      { context: { connection_id: "conn-1" } },
    );
  });

  it("keeps the connection context when resuming an approval", async () => {
    stream.current.interrupt = approvalInterrupt;
    render(<Chat connectionId="conn-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(stream.current.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "approve" }] } },
      context: { connection_id: "conn-1" },
    });
  });
});

describe("ChatPage", () => {
  it("offers the connections in a labelled picker and preselects the first", async () => {
    render(<ChatPage />);

    const picker = await screen.findByLabelText("Connection");
    await waitFor(() => expect(picker).toHaveValue(postgresConnection.id));
    expect(screen.getByRole("option", { name: "Warehouse" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Local demo" })).toBeInTheDocument();
  });

  it("submits with the preselected connection id", async () => {
    render(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByLabelText("Connection")).toHaveValue(postgresConnection.id),
    );

    await send("hello");

    expect(stream.current.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: "hello" }] },
      { context: { connection_id: postgresConnection.id } },
    );
  });

  it("submits with the connection picked by the user", async () => {
    render(<ChatPage />);
    const picker = await screen.findByLabelText("Connection");
    await waitFor(() => expect(picker).toHaveValue(postgresConnection.id));

    await userEvent.selectOptions(picker, duckdbConnection.id);
    await send("hello");

    expect(stream.current.submit).toHaveBeenCalledWith(
      { messages: [{ type: "human", content: "hello" }] },
      { context: { connection_id: duckdbConnection.id } },
    );
  });

  it("links to the connections page when no connection exists", async () => {
    api.listConnections.mockResolvedValue([]);
    render(<ChatPage />);

    const link = await screen.findByRole("link", { name: /connection/i });
    expect(link).toHaveAttribute("href", "/connections");
  });

  it("shows a friendly alert when connections cannot be loaded", async () => {
    api.listConnections.mockRejectedValue(new Error("ECONNREFUSED 10.0.0.5:8000"));
    render(<ChatPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong|could not load/i);
    expect(alert).not.toHaveTextContent("10.0.0.5");
  });
});
