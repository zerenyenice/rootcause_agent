"use client";

import { Database, MessagesSquare, Plus, Table2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { ThemeToggle } from "./ThemeToggle";
import { ThreadList } from "./ThreadList";
import { useThreads } from "./ThreadsProvider";

const LINKS = [
  { href: "/", label: "Chat", icon: MessagesSquare },
  { href: "/connections", label: "Connections", icon: Database },
  { href: "/catalog", label: "Catalog", icon: Table2 },
] as const;

export function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight">
      <span
        aria-hidden="true"
        className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[13px] font-bold text-accent-fg"
      >
        R
      </span>
      RootCause
    </span>
  );
}

export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { newInvestigation } = useThreads();

  return (
    <div className="flex h-full flex-col gap-3 py-3">
      <div className="px-4 pt-1 pb-1">
        <Logo />
      </div>
      <div className="px-3">
        <Button
          variant="primary"
          className="w-full justify-center"
          icon={<Plus className="h-4 w-4" />}
          onClick={() => {
            newInvestigation();
            onNavigate?.();
          }}
        >
          New investigation
        </Button>
      </div>
      <ThreadList onNavigate={onNavigate} />
      <div className="flex flex-col gap-3 border-t border-line px-3 pt-3">
        <nav aria-label="Main">
          <ul className="flex flex-col gap-0.5">
            {LINKS.map(({ href, label, icon: Icon }) => {
              const isCurrent = pathname === href;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={isCurrent ? "page" : undefined}
                    onClick={onNavigate}
                    className={cn(
                      "flex items-center gap-2.5 rounded-control px-2.5 py-1.5 font-medium transition-colors",
                      isCurrent
                        ? "bg-subtle text-fg"
                        : "text-muted hover:bg-subtle hover:text-fg",
                    )}
                  >
                    <Icon aria-hidden="true" className="h-4 w-4" />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <ThemeToggle />
      </div>
    </div>
  );
}
