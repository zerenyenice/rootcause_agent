"use client";

import { useCallback, useEffect, useState } from "react";
import { listConnections, type Connection } from "./api";
import { apiErrorMessage } from "./messages";

export interface ConnectionsState {
  /** null until the first load finishes (successfully or not). */
  connections: Connection[] | null;
  error: string | null;
  reload: () => Promise<void>;
}

/** Loads the connection list once on mount; `reload` refetches without a loading flash. */
export function useConnections(): ConnectionsState {
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setConnections(await listConnections());
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { connections, error, reload };
}
