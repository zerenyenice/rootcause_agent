"use client";

import { useCallback, useEffect, useState } from "react";
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
import { CatalogTableItem } from "./CatalogTableItem";
import { IndexPanel } from "./IndexPanel";

/** Catalog editor for one connection; remounted (keyed) when the connection changes. */
export function CatalogView({ connectionId }: { connectionId: string }) {
  const [tables, setTables] = useState<CatalogTable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isImporting, setIsImporting] = useState(false);

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

  const hasSelection = tables?.some((t) => t.selected) ?? false;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void onImport()}
          disabled={isImporting}
          className="rounded border border-gray-400 px-4 py-2 disabled:opacity-50"
        >
          Import schema
        </button>
        {isImporting && (
          <span role="status" className="text-sm text-gray-600">
            Importing schema...
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="rounded bg-red-50 p-2 text-red-800">
          {error}
        </p>
      )}
      {tables === null && !error && (
        <p role="status" className="text-sm text-gray-600">
          Loading tables...
        </p>
      )}
      {tables?.length === 0 && <p>No tables imported yet. Import the schema to begin.</p>}
      {tables && tables.length > 0 && !hasSelection && (
        <p className="text-yellow-800">
          No tables selected: the assistant will not be able to query anything.
        </p>
      )}
      {tables && tables.length > 0 && (
        <ul aria-label="Tables" className="flex flex-col gap-2">
          {tables.map((table) => (
            <CatalogTableItem
              key={table.id}
              table={table}
              onTablePatch={onTablePatch}
              onColumnPatch={onColumnPatch}
            />
          ))}
        </ul>
      )}
      <IndexPanel connectionId={connectionId} />
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
