"use client";

import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { AgentThreadTransport } from "@/lib/agentTransport";
import { AGENT_URL } from "@/lib/config";
import { buildResume, parseApprovalRequests, type DecisionType } from "@/lib/interrupt";
import { friendlyError } from "@/lib/messages";
import { InterruptPanel } from "./InterruptPanel";
import { MessageItem } from "./MessageItem";

interface ChatState {
  messages: Message[];
}

interface ChatProps {
  /** Database connection the agent should query; omitted means no run context. */
  connectionId?: string | null;
}

export function Chat({ connectionId }: ChatProps) {
  const [transport] = useState(() => new AgentThreadTransport(AGENT_URL));
  // Controlled so "New thread" can clear the conversation: with a custom transport
  // useStream resets its state when threadId changes (it has no switchThread then).
  const [threadId, setThreadId] = useState<string | null>(null);
  const stream = useStream<ChatState>({
    transport,
    messagesKey: "messages",
    threadId,
    onThreadId: setThreadId,
  });
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const approval = stream.interrupt
    ? parseApprovalRequests(stream.interrupt.value)
    : null;
  const isApprovalPending = approval !== null;
  const wasPending = useRef(false);

  // Return focus to the composer once an approval has been resolved.
  useEffect(() => {
    if (wasPending.current && !isApprovalPending) inputRef.current?.focus();
    wasPending.current = isApprovalPending;
  }, [isApprovalPending]);

  const context = connectionId ? { connection_id: connectionId } : undefined;

  function send() {
    const content = draft.trim();
    if (!content || stream.isLoading || isApprovalPending) return;
    setDraft("");
    const input = { messages: [{ type: "human" as const, content }] };
    if (context) void stream.submit(input, { context });
    else void stream.submit(input);
    inputRef.current?.focus();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  }

  function decide(type: DecisionType) {
    const command = { resume: buildResume(type, approval?.length ?? 1) };
    if (context) void stream.submit(null, { command, context });
    else void stream.submit(null, { command });
  }

  function newThread() {
    setDraft("");
    transport.reset();
    setThreadId(null);
    // Typed as always present, but missing at runtime with a custom transport.
    stream.switchThread?.(null);
    inputRef.current?.focus();
  }

  return (
    <main className="mx-auto flex h-full min-h-0 w-full max-w-3xl flex-1 flex-col gap-3 p-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">RootCause</h1>
        <button
          type="button"
          onClick={newThread}
          className="rounded border border-gray-400 px-3 py-1"
        >
          New thread
        </button>
      </header>

      <ul
        role="log"
        aria-relevant="additions"
        aria-label="Conversation"
        className="flex flex-1 flex-col gap-3 overflow-y-auto"
      >
        {stream.messages.map((message, index) => (
          <MessageItem key={message.id ?? index} message={message} />
        ))}
      </ul>

      {stream.isLoading && (
        <p role="status" className="text-sm text-gray-600">
          Thinking...
        </p>
      )}
      {stream.error != null && (
        <p role="alert" className="rounded bg-red-50 p-2 text-red-800">
          {friendlyError(stream.error)}
        </p>
      )}
      {approval && <InterruptPanel requests={approval} onDecision={decide} />}

      {isApprovalPending && (
        <p className="text-sm text-gray-600">
          Sending is disabled until you approve or reject the pending query.
        </p>
      )}
      <form onSubmit={onSubmit} className="flex items-end gap-2">
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={isApprovalPending}
          rows={2}
          placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
          className="flex-1 rounded border border-gray-400 p-2"
        />
        <button
          type="submit"
          disabled={stream.isLoading || isApprovalPending}
          className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Send
        </button>
        <button
          type="button"
          onClick={() => void stream.stop()}
          disabled={!stream.isLoading}
          className="rounded border border-gray-400 px-4 py-2 disabled:opacity-50"
        >
          Stop
        </button>
      </form>
    </main>
  );
}
