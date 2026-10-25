import type { Message } from "@langchain/langgraph-sdk";
import { render, screen } from "@testing-library/react";
import { MessageItem } from "@/components/MessageItem";
import { friendlyError, messageText } from "@/lib/messages";

const list = (m: Message) =>
  render(
    <ul>
      <MessageItem message={m} />
    </ul>,
  );

describe("MessageItem", () => {
  it("renders a human message", () => {
    list({ type: "human", content: "why did sales drop?" });
    expect(screen.getByText("why did sales drop?")).toBeInTheDocument();
  });

  it("renders an assistant message as markdown", () => {
    list({ type: "ai", content: "# Report\n\n**bold**" });
    expect(screen.getByRole("heading", { name: "Report" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });

  it("renders nothing for an assistant message with no text", () => {
    const { container } = list({ type: "ai", content: "" });
    expect(container.querySelector("li")).toBeNull();
  });

  it("shows only the tool name for tool messages, collapsed", () => {
    const { container } = list({
      type: "tool",
      name: "run_sql",
      content: "SECRET-ROWS",
      tool_call_id: "1",
    });
    expect(screen.getByText(/run_sql/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("SECRET-ROWS");
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });

  it("ignores system messages", () => {
    const { container } = list({ type: "system", content: "x" });
    expect(container.querySelector("li")).toBeNull();
  });
});

describe("messages helpers", () => {
  it("flattens content blocks to text", () => {
    const text = messageText({
      type: "ai",
      content: [
        { type: "text", text: "a" },
        { type: "text", text: "b" },
      ],
    });
    expect(text).toBe("a\nb");
  });

  it("does not leak error details", () => {
    expect(friendlyError(new Error("db password=hunter2"))).not.toContain("hunter2");
  });
});
