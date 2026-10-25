import type { Message } from "@langchain/langgraph-sdk";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "@/components/Chat";

interface FakeStream {
  messages: Message[];
  isLoading: boolean;
  error: unknown;
  interrupt: { value: unknown } | undefined;
  submit: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  switchThread: ReturnType<typeof vi.fn>;
}

const state = vi.hoisted(() => ({ current: undefined as unknown }));

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: () => state.current,
}));

function setStream(overrides: Partial<FakeStream> = {}): FakeStream {
  const stream: FakeStream = {
    messages: [],
    isLoading: false,
    error: undefined,
    interrupt: undefined,
    submit: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    switchThread: vi.fn(),
    ...overrides,
  };
  state.current = stream;
  return stream;
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

describe("Chat", () => {
  it("submits a human message on Enter and refocuses the input", async () => {
    const stream = setStream();
    render(<Chat />);
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "hello{Enter}");
    expect(stream.submit).toHaveBeenCalledWith({
      messages: [{ type: "human", content: "hello" }],
    });
    expect(input).toHaveFocus();
    expect(input).toHaveValue("");
  });

  it("adds a newline on Shift+Enter without submitting", async () => {
    const stream = setStream();
    render(<Chat />);
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "a{Shift>}{Enter}{/Shift}b");
    expect(stream.submit).not.toHaveBeenCalled();
    expect(input).toHaveValue("a\nb");
  });

  it("ignores blank input and submits via the Send button", async () => {
    const stream = setStream();
    render(<Chat />);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(stream.submit).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(stream.submit).toHaveBeenCalledTimes(1);
  });

  it("shows the streaming indicator and stops the run", async () => {
    const stream = setStream({ isLoading: true });
    render(<Chat />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(stream.stop).toHaveBeenCalled();
  });

  it("starts a new thread", async () => {
    const stream = setStream();
    render(<Chat />);
    await userEvent.click(screen.getByRole("button", { name: "New thread" }));
    expect(stream.switchThread).toHaveBeenCalledWith(null);
  });

  it("shows a friendly error without raw details", () => {
    setStream({ error: new Error("stack trace secret") });
    render(<Chat />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong/i);
    expect(alert).not.toHaveTextContent("secret");
  });

  it("renders messages in a polite live region", () => {
    setStream({
      messages: [
        { type: "human", content: "q", id: "1" },
        { type: "ai", content: "answer", id: "2" },
      ],
    });
    render(<Chat />);
    expect(screen.getByRole("log", { name: "Conversation" })).toHaveAttribute(
      "aria-relevant",
      "additions",
    );
    expect(screen.getByText("answer")).toBeInTheDocument();
  });

  it("resumes with approve", async () => {
    const stream = setStream({ interrupt: approvalInterrupt });
    render(<Chat />);
    expect(screen.getByText(/Big query, est. 5 GB/)).toBeInTheDocument();
    const sql = screen.getByText("SELECT * FROM t");
    expect(sql).toBeInTheDocument();
    expect(sql.closest("pre")).toHaveClass("font-mono");
    expect(document.body.textContent).not.toContain("LEAKME");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(stream.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "approve" }] } },
    });
  });

  it("resumes with reject", async () => {
    const stream = setStream({ interrupt: approvalInterrupt });
    render(<Chat />);
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(stream.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "reject" }] } },
    });
  });

  it("does not show the approval panel for unrecognised interrupts", () => {
    setStream({ interrupt: { value: "weird" } });
    render(<Chat />);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("disables the composer with a hint while an approval is pending", () => {
    setStream({ interrupt: approvalInterrupt });
    render(<Chat />);
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByText(/Sending is disabled/)).toBeInTheDocument();
  });

  it("focuses the approval heading, then the input once resolved", () => {
    setStream({ interrupt: approvalInterrupt });
    const { rerender } = render(<Chat />);
    expect(screen.getByRole("heading", { name: /Approval required/ })).toHaveFocus();
    setStream({ interrupt: undefined });
    rerender(<Chat />);
    expect(screen.getByLabelText("Message")).toHaveFocus();
  });

  it("shows every statement of a batch and resumes with one decision each", async () => {
    const stream = setStream({
      interrupt: {
        value: {
          action_requests: [
            { name: "run_sql", args: { sql: "SELECT 1" }, description: "first" },
            { name: "run_sql", args: { sql: "SELECT 2" }, description: "second" },
          ],
        },
      },
    });
    render(<Chat />);
    expect(screen.getByText("SELECT 1")).toBeInTheDocument();
    expect(screen.getByText("SELECT 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve all" }));
    expect(stream.submit).toHaveBeenCalledWith(null, {
      command: { resume: { decisions: [{ type: "approve" }, { type: "approve" }] } },
    });
    await userEvent.click(screen.getByRole("button", { name: "Reject all" }));
    expect(stream.submit).toHaveBeenLastCalledWith(null, {
      command: { resume: { decisions: [{ type: "reject" }, { type: "reject" }] } },
    });
  });
});
