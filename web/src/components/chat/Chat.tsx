"use client";

import { useStream, type UseStreamCustom } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";
import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useThreads } from "@/components/shell/ThreadsProvider";
import { buildSegments } from "@/lib/activity";
import { AgentThreadTransport } from "@/lib/agentTransport";
import type { Connection } from "@/lib/api";
import { AGENT_URL } from "@/lib/config";
import { buildResume, parseApprovalRequests, type DecisionType } from "@/lib/interrupt";
import { friendlyError, mergeMessages, messageText } from "@/lib/messages";
import { loadThreadMessages } from "@/lib/threads";
import { ApprovalCard } from "./ApprovalCard";
import { Composer } from "./Composer";
import { ConnectionPill } from "./ConnectionPill";
import { Conversation } from "./Conversation";
import { EmptyChat } from "./EmptyChat";

type ChatState = { messages: Message[] };

interface ChatProps {
  connections: Connection[];
  connection: Connection;
  onConnectionChange: (id: string) => void;
  /** Past thread to continue; null starts a fresh investigation. */
  initialThreadId: string | null;
}

const STICK_THRESHOLD_PX = 80;

export function Chat({
  connections,
  connection,
  onConnectionChange,
  initialThreadId,
}: ChatProps) {
  const { refresh, setActiveThreadId } = useThreads();
  const [transport] = useState(
    () => new AgentThreadTransport(AGENT_URL, (id) => setActiveThreadId(id)),
  );
  const stream = useChatStream(transport);
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<Message[]>([]);
  const [historyState, setHistoryState] = useState<"idle" | "loading" | "failed">(
    initialThreadId ? "loading" : "idle",
  );
  const [durations, setDurations] = useState<Readonly<Record<number, number>>>({});
  const [isPinned, setIsPinned] = useState(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const startedAt = useRef<number | null>(null);
  const turnRef = useRef(0);
  const wasRunning = useRef(false);

  // Continue a past thread: load its messages and point the transport at it.
  useEffect(() => {
    if (!initialThreadId) return;
    let cancelled = false;
    transport.use(initialThreadId);
    void loadThreadMessages(initialThreadId).then((loaded) => {
      if (cancelled) return;
      if (loaded === null) setHistoryState("failed");
      else {
        setHistory(loaded);
        setHistoryState("idle");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [initialThreadId, transport]);

  const interruptValue = readInterruptValue(stream.interrupt);
  const approval =
    interruptValue === undefined ? null : parseApprovalRequests(interruptValue);
  const isApprovalPending = approval !== null;
  const isRunning = stream.isLoading;

  const messages = useMemo(
    () => mergeMessages(history, stream.messages),
    [history, stream.messages],
  );
  const segments = useMemo(
    () =>
      buildSegments(messages, {
        isRunning: isRunning || isApprovalPending,
        subagentMessages: (id) => stream.getSubagent?.(id)?.messages,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stream is a fresh object each render; its inputs below are the real triggers
    [messages, isRunning, isApprovalPending, stream.subagents, stream.messages],
  );
  turnRef.current = segments.filter((s) => s.kind === "human").length;

  // Record how long each run took, for the "N steps · 42s" summary.
  useEffect(() => {
    if (isRunning && !wasRunning.current && startedAt.current === null) {
      startedAt.current = Date.now();
    }
    if (!isRunning && wasRunning.current && startedAt.current !== null) {
      const elapsed = Date.now() - startedAt.current;
      const turn = turnRef.current;
      setDurations((current) => ({ ...current, [turn]: elapsed }));
      startedAt.current = null;
      void refresh();
    }
    wasRunning.current = isRunning;
  }, [isRunning, refresh]);

  // Return focus to the composer once an approval has been resolved.
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !isApprovalPending) inputRef.current?.focus();
    wasPending.current = isApprovalPending;
  }, [isApprovalPending]);

  // Keep the newest content in view unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && isPinned) el.scrollTop = el.scrollHeight;
  }, [segments, isApprovalPending, isRunning, isPinned]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    setIsPinned(el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX);
  }

  const context = useMemo(() => ({ connection_id: connection.id }), [connection.id]);

  const send = useCallback(
    (text: string) => {
      const content = text.trim();
      if (!content || stream.isLoading || isApprovalPending) return;
      setDraft("");
      setIsPinned(true);
      startedAt.current = Date.now();
      void stream.submit(
        { messages: [{ type: "human" as const, content }] },
        { context },
      );
      inputRef.current?.focus();
    },
    [stream, isApprovalPending, context],
  );

  function decide(type: DecisionType) {
    const command = { resume: buildResume(type, approval?.length ?? 1) };
    void stream.submit(null, { command, context });
  }

  function retry() {
    const lastUser = [...messages].reverse().find((m) => m.type === "human");
    if (lastUser) send(messageText(lastUser));
  }

  const hasConversation = segments.length > 0;
  const showEmpty = !hasConversation && historyState === "idle" && !isRunning;
  const liveStatus = isApprovalPending
    ? "Waiting for your approval"
    : isRunning
      ? "The agent is working"
      : "";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <ConnectionPill
          connections={connections}
          value={connection}
          onChange={onConnectionChange}
        />
        <p className="hidden text-xs text-muted sm:block">
          Read-only access to your data
        </p>
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="h-full overflow-y-auto"
          data-testid="chat-scroll"
        >
          <div className="mx-auto flex w-full max-w-[760px] flex-col gap-5 px-4 py-6">
            {historyState === "loading" && (
              <div className="flex flex-col gap-3" aria-label="Loading conversation">
                <Skeleton className="h-10 w-2/3 self-end" />
                <Skeleton className="h-24" />
                <Skeleton className="h-16" />
              </div>
            )}
            {historyState === "failed" && (
              <Banner title="Could not load this conversation">
                It may have been deleted. Start a new investigation from the sidebar.
              </Banner>
            )}
            {showEmpty && (
              <EmptyChat connectionName={connection.name} onPick={(q) => send(q)} />
            )}
            {hasConversation && (
              <Conversation
                segments={segments}
                isRunning={isRunning}
                durations={durations}
              />
            )}
            {isRunning && segments[segments.length - 1]?.kind === "human" && (
              <p className="text-muted">Thinking…</p>
            )}
            {approval && <ApprovalCard requests={approval} onDecision={decide} />}
            {stream.error != null && (
              <Banner
                title="Something went wrong"
                action={
                  <Button size="sm" variant="secondary" onClick={retry}>
                    Retry
                  </Button>
                }
              >
                {friendlyError(stream.error)}
              </Banner>
            )}
          </div>
        </div>
        {!isPinned && hasConversation && (
          <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
            <Button
              size="sm"
              className="pointer-events-auto rounded-full shadow-md"
              icon={<ArrowDown className="h-3.5 w-3.5" />}
              onClick={() => {
                setIsPinned(true);
              }}
            >
              Jump to latest
            </Button>
          </div>
        )}
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {liveStatus}
      </p>
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={() => send(draft)}
        onStop={() => void stream.stop()}
        isRunning={isRunning}
        disabled={isApprovalPending}
        hint={
          isApprovalPending ? "Approve or reject the pending query to continue." : null
        }
        inputRef={inputRef}
      />
    </div>
  );
}

function readInterruptValue(interrupt: unknown): unknown {
  const first: unknown = Array.isArray(interrupt) ? interrupt[0] : interrupt;
  if (typeof first !== "object" || first === null) return undefined;
  return (first as { value?: unknown }).value;
}

/**
 * `useStream` with the custom transport. `filterSubagentMessages` (supported by the
 * SDK but missing from its public option types) routes the root-cause subagent's
 * messages into `getSubagent(...)` instead of the main thread.
 */
function useChatStream(transport: AgentThreadTransport): UseStreamCustom<ChatState> {
  const options = { transport, messagesKey: "messages", filterSubagentMessages: true };
  return useStream<ChatState>(options) as unknown as UseStreamCustom<ChatState>;
}
