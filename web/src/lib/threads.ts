import type { Message } from "@langchain/langgraph-sdk";
import { AGENT_URL } from "./config";

export interface ThreadSummary {
  id: string;
  title: string;
  connectionId: string | null;
  createdAt: string;
  updatedAt: string;
}

export const UNTITLED_THREAD = "New investigation";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toSummary(raw: unknown): ThreadSummary[] {
  if (!isRecord(raw) || typeof raw.thread_id !== "string") return [];
  const metadata = isRecord(raw.metadata) ? raw.metadata : {};
  const created = typeof raw.created_at === "string" ? raw.created_at : "";
  const title = typeof metadata.title === "string" ? metadata.title.trim() : "";
  return [
    {
      id: raw.thread_id,
      title: title || UNTITLED_THREAD,
      connectionId:
        typeof metadata.connection_id === "string" ? metadata.connection_id : null,
      createdAt: created,
      updatedAt: typeof raw.updated_at === "string" ? raw.updated_at : created,
    },
  ];
}

export const THREAD_PAGE_SIZE = 50;

/**
 * Past threads, newest first. Returns null when the service has no history
 * endpoint (older agent service) or is unreachable, so the UI can hide history.
 */
export async function searchThreads(
  limit = THREAD_PAGE_SIZE,
  offset = 0,
): Promise<ThreadSummary[] | null> {
  try {
    const response = await fetch(`${AGENT_URL}/threads/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit, offset }),
    });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (!Array.isArray(body)) return null;
    return body.flatMap(toSummary);
  } catch {
    return null;
  }
}

export async function deleteThread(threadId: string): Promise<boolean> {
  try {
    const response = await fetch(`${AGENT_URL}/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/** Messages of a past thread, or null when it cannot be loaded. */
export async function loadThreadMessages(threadId: string): Promise<Message[] | null> {
  try {
    const response = await fetch(
      `${AGENT_URL}/threads/${encodeURIComponent(threadId)}/state`,
    );
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (!isRecord(body) || !isRecord(body.values)) return null;
    const messages = body.values.messages;
    if (!Array.isArray(messages)) return [];
    return messages.filter(
      (m): m is Message => isRecord(m) && typeof m.type === "string",
    );
  } catch {
    return null;
  }
}
