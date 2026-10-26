"use client";

import { ChevronDown, Database, FileBox } from "lucide-react";
import { Menu } from "@/components/ui/Menu";
import type { Connection } from "@/lib/api";

export function KindIcon({
  kind,
  className,
}: {
  kind: Connection["kind"];
  className?: string;
}) {
  const Icon = kind === "duckdb" ? FileBox : Database;
  return <Icon aria-hidden="true" className={className ?? "h-4 w-4"} />;
}

interface ConnectionPillProps {
  connections: Connection[];
  value: Connection;
  onChange: (id: string) => void;
}

/** Active connection as a pill; switching starts a new thread (the caller remounts). */
export function ConnectionPill({ connections, value, onChange }: ConnectionPillProps) {
  return (
    <Menu
      items={connections.map((connection) => ({
        label: connection.name,
        checked: connection.id === value.id,
        icon: <KindIcon kind={connection.kind} className="h-3.5 w-3.5 text-muted" />,
        onSelect: () => onChange(connection.id),
      }))}
      trigger={(props) => (
        <button
          type="button"
          {...props}
          aria-label={`Connection: ${value.name}`}
          className="inline-flex h-8 items-center gap-2 rounded-full border border-line-strong bg-surface px-3 text-[13px] font-medium transition-colors hover:bg-subtle"
        >
          <KindIcon kind={value.kind} className="h-3.5 w-3.5 text-muted" />
          <span className="max-w-48 truncate">{value.name}</span>
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 text-muted" />
        </button>
      )}
    />
  );
}
