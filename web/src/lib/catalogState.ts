import type { CatalogColumn, CatalogPatch, CatalogTable } from "./api";

/** Immutably apply a patch to one table. */
export function patchTable(
  tables: CatalogTable[],
  tableId: string,
  patch: CatalogPatch,
): CatalogTable[] {
  return tables.map((t) => (t.id === tableId ? { ...t, ...patch } : t));
}

/** Immutably apply a patch to one column of one table. */
export function patchColumn(
  tables: CatalogTable[],
  tableId: string,
  columnId: string,
  patch: CatalogPatch,
): CatalogTable[] {
  return tables.map((t) =>
    t.id === tableId
      ? {
          ...t,
          columns: t.columns.map((c): CatalogColumn =>
            c.id === columnId ? { ...c, ...patch } : c,
          ),
        }
      : t,
  );
}
