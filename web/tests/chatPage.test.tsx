import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPage } from "@/components/chat/ChatPage";
import { duckdbConnection, postgresConnection } from "./fixtures/admin";

const mocks = vi.hoisted(() => ({
  connections: { connections: null as unknown, error: null as string | null },
  session: {
    key: 0,
    threadId: null as string | null,
    connectionId: null as string | null,
  },
  setActiveThreadId: vi.fn(),
}));

vi.mock("@/lib/useConnections", () => ({ useConnections: () => mocks.connections }));
vi.mock("@/components/shell/ThreadsProvider", () => ({
  useThreads: () => ({
    session: mocks.session,
    setActiveThreadId: mocks.setActiveThreadId,
  }),
}));
vi.mock("@/components/chat/Chat", () => ({
  Chat: (props: {
    connection: { id: string; name: string };
    initialThreadId: string | null;
    onConnectionChange: (id: string) => void;
  }) => (
    <div>
      <p>chat:{props.connection.name}</p>
      <p>thread:{props.initialThreadId ?? "none"}</p>
      <button onClick={() => props.onConnectionChange(duckdbConnection.id)}>
        switch
      </button>
    </div>
  ),
}));

beforeEach(() => {
  mocks.connections = {
    connections: [postgresConnection, duckdbConnection],
    error: null,
  };
  mocks.session = { key: 0, threadId: null, connectionId: null };
  mocks.setActiveThreadId.mockClear();
});

describe("ChatPage", () => {
  it("shows an error banner when connections fail to load", () => {
    mocks.connections = { connections: null, error: "Could not reach the server." };
    render(<ChatPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server.");
  });

  it("shows a loading state before connections arrive", () => {
    mocks.connections = { connections: null, error: null };
    render(<ChatPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("guides the user to add a connection when there are none", () => {
    mocks.connections = { connections: [], error: null };
    render(<ChatPage />);
    expect(screen.getByRole("link", { name: "Add a connection" })).toHaveAttribute(
      "href",
      "/connections",
    );
  });

  it("opens the first connection and starts a new thread when switching", async () => {
    render(<ChatPage />);
    expect(screen.getByText("chat:Warehouse")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "switch" }));
    expect(screen.getByText("chat:Local demo")).toBeInTheDocument();
    expect(screen.getByText("thread:none")).toBeInTheDocument();
    expect(mocks.setActiveThreadId).toHaveBeenCalledWith(null);
  });

  it("opens a past thread on its own connection", () => {
    mocks.session = { key: 3, threadId: "t-9", connectionId: duckdbConnection.id };
    render(<ChatPage />);
    expect(screen.getByText("chat:Local demo")).toBeInTheDocument();
    expect(screen.getByText("thread:t-9")).toBeInTheDocument();
  });
});
