"use client";

import { Database } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Skeleton";
import { useConnections } from "@/lib/useConnections";
import { CatalogView } from "./CatalogView";

export function CatalogPage() {
  const { connections, error } = useConnections();
  const requested = useSearchParams().get("connection");
  const [pickedId, setPickedId] = useState<string | null>(null);

  const selectedId =
    connections?.find((c) => c.id === (pickedId ?? requested))?.id ??
    connections?.[0]?.id ??
    null;

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col gap-4 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Catalog</h1>
          <p className="mt-1 text-muted">
            Choose the tables and columns the agent may use, and describe them.
          </p>
        </div>
        {connections && connections.length > 0 && selectedId && (
          <div className="flex items-center gap-2">
            <label htmlFor="catalog-connection" className="text-[13px] font-medium">
              Connection
            </label>
            <Select
              id="catalog-connection"
              value={selectedId}
              onChange={(event) => setPickedId(event.target.value)}
              className="w-56"
            >
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.name}
                </option>
              ))}
            </Select>
          </div>
        )}
      </header>

      {error && <Banner title="Could not load connections">{error}</Banner>}
      {connections === null && !error && (
        <div role="status" aria-label="Loading connections">
          <Skeleton className="h-8 w-64" />
        </div>
      )}
      {connections?.length === 0 && (
        <EmptyState
          icon={<Database className="h-5 w-5" />}
          title="No connections yet"
          description="Add a connection first, then build its catalog."
          action={
            <Link
              href="/connections"
              className="inline-flex h-9 items-center rounded-control bg-accent px-4 font-medium text-accent-fg hover:bg-accent-hover"
            >
              Add a connection
            </Link>
          }
        />
      )}
      {connections && selectedId && (
        <CatalogView key={selectedId} connectionId={selectedId} />
      )}
    </div>
  );
}
