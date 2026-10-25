"use client";

import { useState } from "react";
import { useConnections } from "@/lib/useConnections";
import { Chat } from "./Chat";
import { ConnectionPicker } from "./ConnectionPicker";
import { NoConnections } from "./NoConnections";

export function ChatPage() {
  const { connections, error } = useConnections();
  const [pickedId, setPickedId] = useState<string | null>(null);

  if (error !== null) {
    return (
      <p role="alert" className="m-4 rounded bg-red-50 p-2 text-red-800">
        {error}
      </p>
    );
  }
  if (connections === null) {
    return (
      <p role="status" className="m-4 text-sm text-gray-600">
        Loading connections...
      </p>
    );
  }
  if (connections.length === 0) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4">
        <NoConnections />
      </div>
    );
  }

  const selectedId =
    connections.find((c) => c.id === pickedId)?.id ?? connections[0]?.id ?? "";

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">
      <div className="px-4 pt-3">
        <ConnectionPicker
          connections={connections}
          value={selectedId}
          onChange={setPickedId}
        />
      </div>
      {/* Remount per connection so a thread never mixes two databases. */}
      <Chat key={selectedId} connectionId={selectedId} />
    </div>
  );
}
