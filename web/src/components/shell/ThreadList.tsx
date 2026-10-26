"use client";

import { MessageSquare, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/time";
import type { ThreadSummary } from "@/lib/threads";
import { useThreads } from "./ThreadsProvider";

interface ThreadListProps {
  onNavigate?: () => void;
}

export function ThreadList({ onNavigate }: ThreadListProps) {
  const { threads, isLoading, isAvailable, activeThreadId, openThread, removeThread } =
    useThreads();
  const toast = useToast();
  const [pendingDelete, setPendingDelete] = useState<ThreadSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  if (!isAvailable) return null;

  async function confirmDelete() {
    if (!pendingDelete) return;
    setIsDeleting(true);
    const ok = await removeThread(pendingDelete.id);
    setIsDeleting(false);
    setPendingDelete(null);
    if (!ok) toast.push("Could not delete the investigation.", "bad");
  }

  return (
    <section aria-label="Investigation history" className="flex min-h-0 flex-1 flex-col">
      <h2 className="px-3 pb-1.5 text-[11px] font-medium tracking-wide text-faint uppercase">
        Recent
      </h2>
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {isLoading && (
          <div className="flex flex-col gap-1.5 px-1 pt-1" aria-label="Loading history">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </div>
        )}
        {threads?.length === 0 && (
          <p className="px-2 py-2 text-[13px] text-muted">No investigations yet.</p>
        )}
        <ul className="flex flex-col gap-0.5">
          {threads?.map((thread) => {
            const isActive = thread.id === activeThreadId;
            return (
              <li key={thread.id} className="group relative">
                <button
                  type="button"
                  aria-current={isActive ? "true" : undefined}
                  onClick={() => {
                    openThread(thread);
                    onNavigate?.();
                  }}
                  className={cn(
                    "flex w-full flex-col rounded-control px-2.5 py-1.5 pr-9 text-left transition-colors",
                    isActive ? "bg-subtle" : "hover:bg-subtle",
                  )}
                >
                  <span className="flex items-center gap-1.5">
                    <MessageSquare
                      aria-hidden="true"
                      className="h-3.5 w-3.5 shrink-0 text-faint"
                    />
                    <span className="truncate font-medium">{thread.title}</span>
                  </span>
                  <span className="pl-5 text-xs text-muted">
                    {relativeTime(thread.updatedAt)}
                  </span>
                </button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Delete ${thread.title}`}
                  onClick={() => setPendingDelete(thread)}
                  className="absolute top-1.5 right-1.5 h-7 w-7 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            );
          })}
        </ul>
      </div>
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete investigation?"
        description={`"${pendingDelete?.title ?? ""}" and its conversation will be permanently removed.`}
        confirmLabel="Delete"
        busy={isDeleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </section>
  );
}
