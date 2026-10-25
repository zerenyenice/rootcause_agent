import type { CatalogTable, Connection } from "@/lib/api";

export const postgresConnection: Connection = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Warehouse",
  kind: "postgres",
  host: "db.example.internal",
  port: 5432,
  database: "warehouse",
  user: "reader",
  sslmode: "prefer",
  path: null,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
};

export const duckdbConnection: Connection = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Local demo",
  kind: "duckdb",
  host: null,
  port: null,
  database: null,
  user: null,
  sslmode: null,
  path: "/data/demo.duckdb",
  created_at: "2026-10-02T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};

export const catalogTables: CatalogTable[] = [
  {
    id: "t-orders",
    schema: "public",
    name: "orders",
    selected: true,
    description: "Customer orders",
    row_estimate: 1200,
    columns: [
      {
        id: "c-orders-id",
        name: "id",
        type: "integer",
        nullable: false,
        selected: true,
        description: "Primary key",
      },
      {
        id: "c-orders-status",
        name: "status",
        type: "text",
        nullable: true,
        selected: false,
        description: null,
      },
    ],
  },
  {
    id: "t-events",
    schema: "analytics",
    name: "events",
    selected: false,
    description: null,
    row_estimate: 50000,
    columns: [
      {
        id: "c-events-ts",
        name: "ts",
        type: "timestamp",
        nullable: false,
        selected: false,
        description: null,
      },
    ],
  },
];

/** Deep copy so a test never shares mutable fixture state with another. */
export function freshTables(): CatalogTable[] {
  return structuredClone(catalogTables);
}
