import type { Message } from "@langchain/langgraph-sdk";

/** Flatten message content (string or content blocks) into plain text. */
export function messageText(message: Message): string {
  const { content } = message;
  if (typeof content === "string") return content;
  return content
    .map((block) => (block.type === "text" ? block.text : ""))
    .filter(Boolean)
    .join("\n");
}

/** Map an SDK/network error to a message safe to show to users. */
export function friendlyError(error: unknown): string {
  void error; // details may contain sensitive context; never shown verbatim
  return "Something went wrong while talking to the assistant. Please try again.";
}
