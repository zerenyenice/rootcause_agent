"use client";

import { Database } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useThreads } from "@/components/shell/ThreadsProvider";
import type { Connection } from "@/lib/api";
import { useConnections } from "@/lib/useConnections";
import { Chat } from "./Chat";

/** Picks the active connection; remounting `Chat` per session/connection keeps threads separate. */
function ChatSurface({
  connections,
  initialConnectionId,
  initialThreadId,
}: {
  connections: Connection[];
  initialConnectionId: string | null;
  initialThreadId: string | null;
}) {
  const [pickedId, setPickedId] = useState(initialConnectionId);
  const [threadId, setThreadId] = useState(initialThreadId);
  const { setActiveThreadId } = useThreads();
  const connection =
    connections.find((c) => c.id === pickedId) ?? (connections[0] as Connection);

  return (
    <Chat
      key={`${connection.id}:${threadId ?? "new"}`}
      connections={connections}
      connection={connection}
      initialThreadId={threadId}
      onConnectionChange={(id) => {
        setPickedId(id);
        setThreadId(null); // a thread never mixes two databases
        setActiveThreadId(null);
      }}
    />
  );
}

export function ChatPage() {
  const { connections, error } = useConnections();
  const { session } = useThreads();

  if (error !== null) {
    return (
      <div className="mx-auto w-full max-w-[760px] p-6">
        <Banner title="Could not load connections">{error}</Banner>
      </div>
    );
  }
  if (connections === null) {
    return (
      <div
        role="status"
        aria-label="Loading"
        className="mx-auto w-full max-w-[760px] p-6"
      >
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-6 h-40" />
      </div>
    );
  }
  if (connections.length === 0) {
    return (
      <EmptyState
        icon={<Database className="h-5 w-5" />}
        title="Connect a database to get started"
        description="RootCause needs a read-only connection to investigate your data."
        action={
          <Link
            href="/connections"
            className="inline-flex h-9 items-center rounded-control bg-accent px-4 font-medium text-accent-fg hover:bg-accent-hover"
          >
            Add a connection
          </Link>
        }
      />
    );
  }

  return (
    <ChatSurface
      key={session.key}
      connections={connections}
      initialConnectionId={session.connectionId}
      initialThreadId={session.threadId}
    />
  );
}
