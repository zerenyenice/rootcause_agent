"use client";

import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { API_URL, ASSISTANT_ID } from "@/lib/config";
import { buildResume, parseApprovalRequest, type DecisionType } from "@/lib/interrupt";
import { friendlyError } from "@/lib/messages";
import { InterruptPanel } from "./InterruptPanel";
import { MessageItem } from "./MessageItem";

interface ChatState {
  messages: Message[];
}

export function Chat() {
  const stream = useStream<ChatState>({
    apiUrl: API_URL,
    assistantId: ASSISTANT_ID,
    messagesKey: "messages",
  });
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const approval = stream.interrupt ? parseApprovalRequest(stream.interrupt.value) : null;

  function send() {
    const content = draft.trim();
    if (!content || stream.isLoading) return;
    setDraft("");
    void stream.submit({ messages: [{ type: "human", content }] });
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
    void stream.submit(null, { command: { resume: buildResume(type) } });
  }

  function newThread() {
    setDraft("");
    stream.switchThread(null);
    inputRef.current?.focus();
  }

  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col gap-3 p-4">
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
        aria-live="polite"
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
      {approval && <InterruptPanel request={approval} onDecision={decide} />}

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
          rows={2}
          placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
          className="flex-1 rounded border border-gray-400 p-2"
        />
        <button
          type="submit"
          disabled={stream.isLoading}
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
