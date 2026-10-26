import type { Message } from "@langchain/langgraph-sdk";
import { ApiError } from "@/lib/api";
import {
  apiErrorMessage,
  friendlyError,
  mergeMessages,
  messageText,
} from "@/lib/messages";

describe("messages helpers", () => {
  it("flattens string and block content", () => {
    expect(messageText({ type: "human", content: "hi" })).toBe("hi");
    expect(
      messageText({
        type: "ai",
        content: [
          { type: "text", text: "a" },
          { type: "image_url", image_url: "x" },
          { type: "text", text: "b" },
        ],
      } as Message),
    ).toBe("a\nb");
  });

  it("never leaks raw error text", () => {
    expect(friendlyError(new Error("stack: secret"))).not.toContain("secret");
  });

  it("uses curated API messages and a generic fallback", () => {
    expect(apiErrorMessage(new ApiError("c", "Name is taken", 409))).toBe(
      "Name is taken",
    );
    expect(apiErrorMessage(new Error("boom"))).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("merges by id, later wins, keeping first position", () => {
    const base: Message[] = [
      { type: "human", id: "1", content: "q" },
      { type: "ai", id: "2", content: "old" },
    ];
    const live: Message[] = [
      { type: "ai", id: "2", content: "new" },
      { type: "ai", id: "3", content: "extra" },
      { type: "human", content: "no id" },
    ];
    const merged = mergeMessages(base, live);
    expect(merged.map((m) => m.id ?? "none")).toEqual(["1", "2", "3", "none"]);
    expect(merged[1]?.content).toBe("new");
  });
});
