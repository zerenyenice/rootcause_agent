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

const SERVICE_ERROR_CODE = /^[a-z]+(_[a-z]+)+$/;
const MAX_SERVICE_MESSAGE_CHARS = 300;

/** Map an SDK/network error to a message safe to show to users. */
export function friendlyError(error: unknown): string {
  // The agent service reports refused runs as `event: error` with a snake_case code
  // (connection_unavailable, ...) and a user-safe message. Anything else (network
  // failures, stack text) may contain sensitive detail and is never shown verbatim.
  if (error instanceof Error && SERVICE_ERROR_CODE.test(error.name)) {
    const message = error.message.trim();
    if (message && message.length <= MAX_SERVICE_MESSAGE_CHARS) return message;
  }
  return "Something went wrong while talking to the assistant. Please try again.";
}

/** Message for a failed management API call: API errors are curated, anything else is generic. */
export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}

/** Merge by message id (later wins, first position kept); id-less messages are appended. */
export function mergeMessages(
  base: readonly Message[],
  live: readonly Message[],
): Message[] {
  const merged: Message[] = [];
  const positions = new Map<string, number>();
  for (const message of [...base, ...live]) {
    const position = message.id ? positions.get(message.id) : undefined;
    if (position !== undefined) {
      merged[position] = message;
      continue;
    }
    if (message.id) positions.set(message.id, merged.length);
    merged.push(message);
  }
  return merged;
}
