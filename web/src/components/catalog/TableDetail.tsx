"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Field";
import type { CatalogColumn, CatalogPatch, CatalogTable } from "@/lib/api";
import { DescriptionField } from "./DescriptionField";

interface TableDetailProps {
  table: CatalogTable;
  onTablePatch: (table: CatalogTable, patch: CatalogPatch) => Promise<boolean>;
  onColumnPatch: (
    table: CatalogTable,
    column: CatalogColumn,
    patch: CatalogPatch,
  ) => Promise<boolean>;
}

export function TableDetail({ table, onTablePatch, onColumnPatch }: TableDetailProps) {
  const [filter, setFilter] = useState("");
  const qualified = `${table.schema}.${table.name}`;
  const columns = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return table.columns;
    return table.columns.filter(
      (c) =>
        c.name.toLowerCase().includes(needle) || c.type.toLowerCase().includes(needle),
    );
  }, [table.columns, filter]);

  return (
    <section
      aria-label={`Details of ${qualified}`}
      className="flex min-h-0 flex-col overflow-y-auto rounded-card border border-line bg-surface"
    >
      <header className="border-b border-line p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">{table.name}</h2>
          <span className="text-muted">{table.schema}</span>
          <Badge tone={table.selected ? "ok" : "neutral"}>
            {table.selected ? "Included" : "Excluded"}
          </Badge>
        </div>
        <div className="mt-3">
          <DescriptionField
            multiline
            label={`Description for ${qualified}`}
            placeholder="Describe what this table holds. The agent reads this to pick the right tables."
            value={table.description}
            onSave={(description) => onTablePatch(table, { description })}
          />
        </div>
      </header>

      <div className="flex items-center justify-between gap-3 px-4 pt-4">
        <h3 className="font-semibold">Columns ({table.columns.length})</h3>
        {table.columns.length > 8 && (
          <div className="relative w-56">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-2 left-2.5 h-4 w-4 text-faint"
            />
            <Input
              type="search"
              aria-label="Filter columns"
              placeholder="Filter columns"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              className="h-8 pl-8 text-[13px]"
            />
          </div>
        )}
      </div>

      <div className="overflow-x-auto p-4">
        <table className="w-full min-w-[560px] border-collapse text-left">
          <caption className="sr-only">{`Columns of ${qualified}`}</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="w-12 pb-2 font-medium">
                Include
              </th>
              <th scope="col" className="pb-2 font-medium">
                Name
              </th>
              <th scope="col" className="pb-2 font-medium">
                Type
              </th>
              <th scope="col" className="w-[40%] pb-2 font-medium">
                Description
              </th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => {
              const name = `${qualified}.${column.name}`;
              return (
                <tr key={column.id} className="border-b border-line last:border-b-0">
                  <td className="py-2 align-middle">
                    <input
                      type="checkbox"
                      aria-label={`Include ${name}`}
                      checked={column.selected}
                      onChange={(event) =>
                        void onColumnPatch(table, column, {
                          selected: event.target.checked,
                        })
                      }
                      className="h-4 w-4 accent-accent"
                    />
                  </td>
                  <td className="py-2 pr-3 font-medium">{column.name}</td>
                  <td className="py-2 pr-3 font-mono text-xs text-muted">
                    {column.type}
                    {column.nullable && (
                      <span className="ml-1.5 text-faint">nullable</span>
                    )}
                  </td>
                  <td className="py-1.5">
                    <DescriptionField
                      label={`Description for ${name}`}
                      value={column.description}
                      onSave={(description) =>
                        onColumnPatch(table, column, { description })
                      }
                    />
                  </td>
                </tr>
              );
            })}
            {columns.length === 0 && (
              <tr>
                <td colSpan={4} className="py-6 text-center text-muted">
                  No columns match “{filter}”.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
