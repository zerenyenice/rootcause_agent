import type { Message } from "@langchain/langgraph-sdk";
import { ApiError } from "./api";

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

/** Message for a failed management API call: API errors are curated, anything else is generic. */
export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}
