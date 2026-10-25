"use client";

import { useState } from "react";
import type { CatalogColumn, CatalogPatch, CatalogTable } from "@/lib/api";
import { DescriptionField } from "./DescriptionField";

interface CatalogTableItemProps {
  table: CatalogTable;
  onTablePatch: (table: CatalogTable, patch: CatalogPatch) => Promise<boolean>;
  onColumnPatch: (
    table: CatalogTable,
    column: CatalogColumn,
    patch: CatalogPatch,
  ) => Promise<boolean>;
}

export function CatalogTableItem({
  table,
  onTablePatch,
  onColumnPatch,
}: CatalogTableItemProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const qualified = `${table.schema}.${table.name}`;
  const panelId = `columns-${table.id}`;

  return (
    <li className="flex flex-col gap-2 rounded border border-gray-300 p-3">
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id={`table-${table.id}`}
          checked={table.selected}
          onChange={(event) =>
            void onTablePatch(table, { selected: event.target.checked })
          }
        />
        <label htmlFor={`table-${table.id}`} className="font-medium">
          {qualified}
        </label>
        {table.row_estimate !== null && (
          <span className="text-sm text-gray-600">~{table.row_estimate} rows</span>
        )}
        <button
          type="button"
          aria-expanded={isExpanded}
          aria-controls={panelId}
          onClick={() => setIsExpanded((current) => !current)}
          className="ml-auto rounded border border-gray-400 px-2 py-1 text-sm"
        >
          Columns ({table.columns.length})
        </button>
      </div>
      <DescriptionField
        label={`Description for ${qualified}`}
        value={table.description}
        onSave={(description) => onTablePatch(table, { description })}
      />
      {isExpanded && (
        <ul
          id={panelId}
          aria-label={`Columns of ${qualified}`}
          className="ml-6 flex flex-col gap-2"
        >
          {table.columns.map((column) => {
            const columnName = `${qualified}.${column.name}`;
            return (
              <li key={column.id} className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id={`column-${column.id}`}
                    checked={column.selected}
                    onChange={(event) =>
                      void onColumnPatch(table, column, {
                        selected: event.target.checked,
                      })
                    }
                  />
                  <label htmlFor={`column-${column.id}`}>{columnName}</label>
                  <span className="text-sm text-gray-600">{column.type}</span>
                  {column.nullable && (
                    <span className="text-sm text-gray-600">nullable</span>
                  )}
                </div>
                <DescriptionField
                  label={`Description for ${columnName}`}
                  value={column.description}
                  onSave={(description) => onColumnPatch(table, column, { description })}
                />
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
