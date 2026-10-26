"use client";

import { Database, Plus } from "lucide-react";
import { useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import {
  deleteConnection,
  testConnection,
  type Connection,
  type ConnectionProbe,
} from "@/lib/api";
import { apiErrorMessage } from "@/lib/messages";
import { useConnections } from "@/lib/useConnections";
import { AddConnectionDialog } from "./AddConnectionDialog";
import { ConnectionCard, type ProbeState } from "./ConnectionCard";

export function ConnectionsPage() {
  const { connections, error: loadError, reload } = useConnections();
  const toast = useToast();
  const [actionError, setActionError] = useState<string | null>(null);
  const [probes, setProbes] = useState<Readonly<Record<string, ProbeState>>>({});
  const [isAdding, setIsAdding] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Connection | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const error = actionError ?? loadError;

  function setProbe(id: string, state: ProbeState | undefined) {
    setProbes((current) => {
      const next = { ...current };
      if (state) next[id] = state;
      else delete next[id];
      return next;
    });
  }

  async function onTest(connection: Connection) {
    setActionError(null);
    setProbe(connection.id, { phase: "testing" });
    try {
      const probe = await testConnection(connection.id);
      setProbe(connection.id, { phase: "done", probe });
    } catch (err) {
      setProbe(connection.id, undefined);
      setActionError(apiErrorMessage(err));
    }
  }

  async function onConfirmDelete() {
    if (!pendingDelete) return;
    setIsDeleting(true);
    setActionError(null);
    try {
      await deleteConnection(pendingDelete.id);
      toast.push(`Deleted ${pendingDelete.name}`);
      await reload();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setIsDeleting(false);
      setPendingDelete(null);
    }
  }

  async function onAdded(id: string, probe: ConnectionProbe | null) {
    setIsAdding(false);
    if (probe) setProbe(id, { phase: "done", probe });
    toast.push("Connection added");
    await reload();
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Connections</h1>
          <p className="mt-1 text-muted">Databases RootCause can investigate.</p>
        </div>
        <Button
          variant="primary"
          icon={<Plus className="h-4 w-4" />}
          onClick={() => setIsAdding(true)}
        >
          Add connection
        </Button>
      </header>

      {error && (
        <div className="mt-6">
          <Banner>{error}</Banner>
        </div>
      )}

      {connections === null && !loadError && (
        <div
          role="status"
          aria-label="Loading connections"
          className="mt-6 grid gap-4 sm:grid-cols-2"
        >
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      )}

      {connections?.length === 0 && (
        <EmptyState
          icon={<Database className="h-5 w-5" />}
          title="No connections yet"
          description="Add a Postgres or DuckDB database to start asking why."
          action={
            <Button variant="primary" onClick={() => setIsAdding(true)}>
              Add connection
            </Button>
          }
        />
      )}

      {connections && connections.length > 0 && (
        <ul aria-label="Connections" className="mt-6 grid gap-4 sm:grid-cols-2">
          {connections.map((connection) => (
            <ConnectionCard
              key={connection.id}
              connection={connection}
              probeState={probes[connection.id]}
              onTest={(c) => void onTest(c)}
              onDelete={setPendingDelete}
            />
          ))}
        </ul>
      )}

      <AddConnectionDialog
        open={isAdding}
        onClose={() => setIsAdding(false)}
        onAdded={(id, probe) => void onAdded(id, probe)}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete connection?"
        description={`"${pendingDelete?.name ?? ""}" and its catalog will be removed. This cannot be undone.`}
        confirmLabel="Delete"
        busy={isDeleting}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
