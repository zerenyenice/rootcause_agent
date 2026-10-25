"use client";

import { useState } from "react";
import { deleteConnection, testConnection, type Connection } from "@/lib/api";
import { apiErrorMessage } from "@/lib/messages";
import { useConnections } from "@/lib/useConnections";
import { ConnectionForm } from "./ConnectionForm";
import { ConnectionRow, type ProbeState } from "./ConnectionRow";

export function ConnectionsPage() {
  const { connections, error: loadError, reload } = useConnections();
  const [actionError, setActionError] = useState<string | null>(null);
  const [probes, setProbes] = useState<Readonly<Record<string, ProbeState>>>({});

  const error = actionError ?? loadError;

  function setProbe(id: string, state: ProbeState) {
    setProbes((current) => ({ ...current, [id]: state }));
  }

  async function onTest(connection: Connection) {
    setActionError(null);
    setProbe(connection.id, { phase: "testing" });
    try {
      const probe = await testConnection(connection.id);
      setProbe(connection.id, { phase: "done", probe });
    } catch (err) {
      setProbes((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([id]) => id !== connection.id),
        ),
      );
      setActionError(apiErrorMessage(err));
    }
  }

  async function onDelete(connection: Connection) {
    if (
      !window.confirm(`Delete connection "${connection.name}"? This cannot be undone.`)
    ) {
      return;
    }
    setActionError(null);
    try {
      await deleteConnection(connection.id);
      await reload();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="text-xl font-semibold">Connections</h1>
      {error && (
        <p role="alert" className="rounded bg-red-50 p-2 text-red-800">
          {error}
        </p>
      )}
      {connections === null && !loadError && (
        <p role="status" className="text-sm text-gray-600">
          Loading connections...
        </p>
      )}
      {connections?.length === 0 && <p>No connections yet. Add one below.</p>}
      {connections && connections.length > 0 && (
        <ul aria-label="Connections" className="flex flex-col gap-2">
          {connections.map((connection) => (
            <ConnectionRow
              key={connection.id}
              connection={connection}
              probeState={probes[connection.id]}
              onTest={(c) => void onTest(c)}
              onDelete={(c) => void onDelete(c)}
            />
          ))}
        </ul>
      )}
      <ConnectionForm onCreated={reload} onError={setActionError} />
    </main>
  );
}
