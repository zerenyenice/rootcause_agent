"use client";

import { useCallback, useEffect, useState } from "react";
import { buildIndex, indexStatus, type IndexStatus } from "./api";
import { apiErrorMessage } from "./messages";

const POLL_INTERVAL_MS = 2000;

export interface IndexStatusState {
  index: IndexStatus | null;
  /** The catalog changed after the last successful build. */
  isStale: boolean;
  isStarting: boolean;
  error: string | null;
  build: () => Promise<void>;
  markStale: () => void;
}

/** Search-index status for a connection; polls while a build is running. */
export function useIndexStatus(connectionId: string): IndexStatusState {
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [isStale, setIsStale] = useState(false);
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
        if (status.status === "running") {
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        }
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

  const build = useCallback(async () => {
    setError(null);
    setIsStarting(true);
    try {
      setIndex(await buildIndex(connectionId));
      setIsStale(false);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setIsStarting(false);
    }
  }, [connectionId]);

  const markStale = useCallback(() => setIsStale(true), []);

  return { index, isStale, isStarting, error, build, markStale };
}
