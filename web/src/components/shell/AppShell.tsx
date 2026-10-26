"use client";

import { Menu as MenuIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { ToastProvider } from "@/components/ui/Toast";
import { Logo, SidebarContent } from "./Sidebar";
import { ThreadsProvider } from "./ThreadsProvider";

/** Sidebar + main area. Below `lg` the sidebar becomes a drawer. */
export function AppShell({ children }: { children: ReactNode }) {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  useEffect(() => {
    if (!isDrawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsDrawerOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isDrawerOpen]);

  return (
    <ToastProvider>
      <ThreadsProvider>
        <div className="flex h-dvh">
          <aside
            aria-label="Sidebar"
            className="hidden w-60 shrink-0 border-r border-line bg-sidebar lg:block"
          >
            <SidebarContent />
          </aside>

          {isDrawerOpen && (
            <div className="fixed inset-0 z-40 lg:hidden">
              <div
                aria-hidden="true"
                className="absolute inset-0 bg-black/50"
                onClick={() => setIsDrawerOpen(false)}
              />
              <aside
                aria-label="Sidebar"
                className="rc-fade-in relative h-full w-64 border-r border-line bg-sidebar shadow-xl"
              >
                <SidebarContent onNavigate={() => setIsDrawerOpen(false)} />
              </aside>
            </div>
          )}

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3 lg:hidden">
              <Button
                size="icon"
                variant="ghost"
                aria-label="Open menu"
                onClick={() => setIsDrawerOpen(true)}
              >
                <MenuIcon className="h-4 w-4" />
              </Button>
              <Logo />
            </header>
            <main id="main" className="flex min-h-0 flex-1 flex-col overflow-y-auto">
              {children}
            </main>
          </div>
        </div>
      </ThreadsProvider>
    </ToastProvider>
  );
}
