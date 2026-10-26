"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import type { CatalogTable } from "@/lib/api";

interface TableListProps {
  tables: CatalogTable[];
  activeId: string | null;
  onOpen: (tableId: string) => void;
  onToggle: (table: CatalogTable, selected: boolean) => void;
  onSetAll: (selected: boolean, tables: CatalogTable[]) => void;
}

function formatRows(count: number): string {
  if (count >= 1_000_000) return `~${(count / 1_000_000).toFixed(1)}M rows`;
  if (count >= 1_000) return `~${(count / 1_000).toFixed(1)}k rows`;
  return `~${count} rows`;
}

export function TableList({
  tables,
  activeId,
  onOpen,
  onToggle,
  onSetAll,
}: TableListProps) {
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return tables;
    return tables.filter((t) => `${t.schema}.${t.name}`.toLowerCase().includes(needle));
  }, [tables, query]);

  return (
    <div className="flex min-h-0 flex-col rounded-card border border-line bg-surface">
      <div className="flex flex-col gap-2 border-b border-line p-3">
        <div className="relative">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-faint"
          />
          <Input
            type="search"
            aria-label="Search tables"
            placeholder="Search tables"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-8"
          />
        </div>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => onSetAll(true, visible)}>
            Select all
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onSetAll(false, visible)}>
            Select none
          </Button>
        </div>
      </div>
      <ul aria-label="Tables" className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {visible.length === 0 && (
          <li className="px-3 py-6 text-center text-muted">No tables match “{query}”.</li>
        )}
        {visible.map((table) => {
          const qualified = `${table.schema}.${table.name}`;
          const isActive = table.id === activeId;
          return (
            <li
              key={table.id}
              className={cn(
                "flex items-center gap-2.5 rounded-control px-2 py-1.5 transition-colors",
                isActive ? "bg-accent/10" : "hover:bg-subtle",
              )}
            >
              <input
                type="checkbox"
                aria-label={`Include ${qualified}`}
                checked={table.selected}
                onChange={(event) => onToggle(table, event.target.checked)}
                className="h-4 w-4 shrink-0 accent-accent"
              />
              <button
                type="button"
                aria-current={isActive ? "true" : undefined}
                aria-label={`Open ${qualified}`}
                onClick={() => onOpen(table.id)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <span className="truncate">
                  <span className={cn("font-medium", !table.selected && "text-muted")}>
                    {table.name}
                  </span>{" "}
                  <span className="text-xs text-faint">{table.schema}</span>
                </span>
                {table.description?.trim() && (
                  <span
                    role="img"
                    aria-label="Has description"
                    title="Has description"
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent"
                  />
                )}
                {table.row_estimate !== null && (
                  <span className="ml-auto shrink-0 text-xs text-muted">
                    {formatRows(table.row_estimate)}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
