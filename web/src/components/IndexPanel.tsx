"use client";

import { useEffect, useState } from "react";
import { buildIndex, indexStatus, type IndexStatus } from "@/lib/api";
import { apiErrorMessage } from "@/lib/messages";

const POLL_INTERVAL_MS = 2000;

function describe(index: IndexStatus | null): string {
  if (index === null) return "Index status unknown";
  if (index.status === "idle") return "Index not built yet";
  return `Index ${index.status}: ${index.indexed_tables} tables, ${index.indexed_columns} columns`;
}

/** Builds the search index and polls its status while a build is running. */
export function IndexPanel({ connectionId }: { connectionId: string }) {
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isRunning = index?.status === "running";

  // Initial status, so a build started earlier is picked up (and polled).
  useEffect(() => {
    let cancelled = false;
    indexStatus(connectionId)
      .then((status) => {
        if (!cancelled) setIndex(status);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(apiErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [connectionId]);

  // Poll one request at a time; stops on any non-running status, error or unmount.
  useEffect(() => {
    if (!isRunning) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const status = await indexStatus(connectionId);
        if (cancelled) return;
        setIndex(status);
        if (status.status === "running")
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        setError(apiErrorMessage(err));
        setIndex(null);
      }
    };
    timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [connectionId, isRunning]);

  async function onBuild() {
    setError(null);
    setIsStarting(true);
    try {
      setIndex(await buildIndex(connectionId));
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setIsStarting(false);
    }
  }

  return (
    <section aria-labelledby="index-heading" className="flex flex-col gap-2">
      <h2 id="index-heading" className="text-lg font-semibold">
        Search index
      </h2>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void onBuild()}
          disabled={isRunning || isStarting}
          className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Build index
        </button>
        <div role="status" aria-label="Index status" className="text-sm text-gray-700">
          {describe(index)}
        </div>
      </div>
      {error && (
        <p role="alert" className="rounded bg-red-50 p-2 text-red-800">
          {error}
        </p>
      )}
    </section>
  );
}
