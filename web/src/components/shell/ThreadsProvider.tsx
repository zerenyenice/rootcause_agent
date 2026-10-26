"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { deleteThread, searchThreads, type ThreadSummary } from "@/lib/threads";

/** What the chat page should show: bumping `key` remounts it on a fresh state. */
export interface ChatSession {
  key: number;
  threadId: string | null;
  connectionId: string | null;
}

interface ThreadsApi {
  /** null while loading or when the service has no history endpoint. */
  threads: ThreadSummary[] | null;
  isLoading: boolean;
  isAvailable: boolean;
  session: ChatSession;
  activeThreadId: string | null;
  refresh: () => Promise<void>;
  openThread: (thread: ThreadSummary) => void;
  newInvestigation: () => void;
  removeThread: (threadId: string) => Promise<boolean>;
  setActiveThreadId: (threadId: string | null) => void;
}

const ThreadsContext = createContext<ThreadsApi | null>(null);

export function ThreadsProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAvailable, setIsAvailable] = useState(true);
  const [session, setSession] = useState<ChatSession>({
    key: 0,
    threadId: null,
    connectionId: null,
  });
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const found = await searchThreads();
    setIsAvailable(found !== null);
    setThreads(found);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const start = useCallback(
    (threadId: string | null, connectionId: string | null) => {
      setSession((current) => ({ key: current.key + 1, threadId, connectionId }));
      setActiveThreadId(threadId);
      if (pathname !== "/") router.push("/");
    },
    [pathname, router],
  );

  const openThread = useCallback(
    (thread: ThreadSummary) => start(thread.id, thread.connectionId),
    [start],
  );

  const newInvestigation = useCallback(() => start(null, null), [start]);

  const removeThread = useCallback(
    async (threadId: string) => {
      const ok = await deleteThread(threadId);
      if (!ok) return false;
      setThreads((current) => current && current.filter((t) => t.id !== threadId));
      if (threadId === activeThreadId) start(null, null);
      return true;
    },
    [activeThreadId, start],
  );

  const value = useMemo<ThreadsApi>(
    () => ({
      threads,
      isLoading,
      isAvailable,
      session,
      activeThreadId,
      refresh,
      openThread,
      newInvestigation,
      removeThread,
      setActiveThreadId,
    }),
    [
      threads,
      isLoading,
      isAvailable,
      session,
      activeThreadId,
      refresh,
      openThread,
      newInvestigation,
      removeThread,
    ],
  );

  return <ThreadsContext.Provider value={value}>{children}</ThreadsContext.Provider>;
}

export function useThreads(): ThreadsApi {
  const context = useContext(ThreadsContext);
  if (!context) throw new Error("useThreads must be used inside ThreadsProvider");
  return context;
}
