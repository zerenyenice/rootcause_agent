"use client";

import { useState } from "react";
import { useConnections } from "@/lib/useConnections";
import { CatalogView } from "./CatalogView";
import { ConnectionPicker } from "./ConnectionPicker";
import { NoConnections } from "./NoConnections";

export function CatalogPage() {
  const { connections, error } = useConnections();
  const [pickedId, setPickedId] = useState<string | null>(null);

  const selectedId =
    connections?.find((c) => c.id === pickedId)?.id ?? connections?.[0]?.id ?? null;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="text-xl font-semibold">Catalog</h1>
      {error && (
        <p role="alert" className="rounded bg-red-50 p-2 text-red-800">
          {error}
        </p>
      )}
      {connections === null && !error && (
        <p role="status" className="text-sm text-gray-600">
          Loading connections...
        </p>
      )}
      {connections?.length === 0 && <NoConnections />}
      {connections && selectedId && (
        <>
          <ConnectionPicker
            connections={connections}
            value={selectedId}
            onChange={setPickedId}
          />
          <CatalogView key={selectedId} connectionId={selectedId} />
        </>
      )}
    </main>
  );
}
