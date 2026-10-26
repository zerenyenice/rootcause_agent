"use client";

import { DatabaseZap, Download, MousePointerClick } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import {
  importCatalog,
  listCatalogTables,
  updateColumn,
  updateTable,
  type CatalogColumn,
  type CatalogPatch,
  type CatalogTable,
} from "@/lib/api";
import { patchColumn, patchTable } from "@/lib/catalogState";
import { apiErrorMessage } from "@/lib/messages";
import { useIndexStatus } from "@/lib/useIndexStatus";
import { IndexControls } from "./IndexControls";
import { TableDetail } from "./TableDetail";
import { TableList } from "./TableList";

/** Catalog editor for one connection; remounted (keyed) when the connection changes. */
export function CatalogView({ connectionId }: { connectionId: string }) {
  const toast = useToast();
  const [tables, setTables] = useState<CatalogTable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const indexState = useIndexStatus(connectionId);
  const { markStale } = indexState;

  const load = useCallback(async () => {
    try {
      setTables(await listCatalogTables(connectionId));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }, [connectionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onImport() {
    setError(null);
    setIsImporting(true);
    try {
      await importCatalog(connectionId);
      await load();
      markStale();
      toast.push("Schema imported");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setIsImporting(false);
    }
  }

  /** Optimistic update: apply now, revert and alert if the server rejects it. */
  async function applyPatch(
    patch: CatalogPatch,
    apply: (current: CatalogTable[], p: CatalogPatch) => CatalogTable[],
    revert: CatalogPatch,
    save: () => Promise<unknown>,
  ): Promise<boolean> {
    setError(null);
    setTables((current) => current && apply(current, patch));
    try {
      await save();
      markStale();
      return true;
    } catch (err) {
      setTables((current) => current && apply(current, revert));
      setError(apiErrorMessage(err));
      return false;
    }
  }

  function onTablePatch(table: CatalogTable, patch: CatalogPatch) {
    return applyPatch(
      patch,
      (current, p) => patchTable(current, table.id, p),
      previous(table, patch),
      () => updateTable(table.id, patch),
    );
  }

  function onColumnPatch(
    table: CatalogTable,
    column: CatalogColumn,
    patch: CatalogPatch,
  ) {
    return applyPatch(
      patch,
      (current, p) => patchColumn(current, table.id, column.id, p),
      previous(column, patch),
      () => updateColumn(column.id, patch),
    );
  }

  function onSetAll(selected: boolean, subset: CatalogTable[]) {
    for (const table of subset) {
      if (table.selected !== selected) void onTablePatch(table, { selected });
    }
  }

  const selectedCount = tables?.filter((t) => t.selected).length ?? 0;
  const describedCount = tables?.filter((t) => t.description?.trim()).length ?? 0;
  const active = tables?.find((t) => t.id === activeId) ?? tables?.[0] ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted" aria-live="polite">
          {tables
            ? `${selectedCount} of ${tables.length} tables selected · ${describedCount} described`
            : " "}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            icon={<Download className="h-3.5 w-3.5" />}
            loading={isImporting}
            onClick={() => void onImport()}
          >
            Import schema
          </Button>
          <IndexControls
            index={indexState.index}
            isStale={indexState.isStale}
            isStarting={indexState.isStarting}
            onBuild={() => void indexState.build()}
          />
        </div>
      </div>

      {(error ?? indexState.error) && <Banner>{error ?? indexState.error}</Banner>}

      {tables === null && !error && (
        <div
          role="status"
          aria-label="Loading tables"
          className="grid gap-4 lg:grid-cols-[320px_1fr]"
        >
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      )}

      {tables?.length === 0 && (
        <EmptyState
          icon={<DatabaseZap className="h-5 w-5" />}
          title="Import the schema to get started"
          description="Pull the tables and columns from this database, then choose what the agent may use."
          action={
            <Button
              variant="primary"
              loading={isImporting}
              onClick={() => void onImport()}
            >
              Import schema
            </Button>
          }
        />
      )}

      {tables && tables.length > 0 && selectedCount === 0 && (
        <Banner tone="warn" alert={false}>
          No tables selected: the assistant will not be able to query anything.
        </Banner>
      )}

      {tables && tables.length > 0 && (
        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[320px_1fr]">
          <TableList
            tables={tables}
            activeId={active?.id ?? null}
            onOpen={setActiveId}
            onToggle={(table, selected) => void onTablePatch(table, { selected })}
            onSetAll={onSetAll}
          />
          {active ? (
            <TableDetail
              key={active.id}
              table={active}
              onTablePatch={onTablePatch}
              onColumnPatch={onColumnPatch}
            />
          ) : (
            <EmptyState
              icon={<MousePointerClick className="h-5 w-5" />}
              title="Select a table"
              description="Pick a table on the left to edit its description and columns."
            />
          )}
        </div>
      )}
    </div>
  );
}

/** The current values of the fields a patch touches, used to roll back. */
function previous(
  item: { selected: boolean; description: string | null },
  patch: CatalogPatch,
): CatalogPatch {
  return {
    ...(patch.selected !== undefined && { selected: item.selected }),
    ...(patch.description !== undefined && { description: item.description ?? "" }),
  };
}
