import { API_URL } from "./config";

export type ConnectionKind = "postgres" | "duckdb";
export type SslMode = "disable" | "prefer" | "require" | "verify-full";

export interface Connection {
  id: string;
  name: string;
  kind: ConnectionKind;
  host: string | null;
  port: number | null;
  database: string | null;
  user: string | null;
  sslmode: SslMode | null;
  path: string | null;
  created_at: string;
  updated_at: string;
}

export interface PostgresConnectionBody {
  name: string;
  kind: "postgres";
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  sslmode: SslMode;
}

export interface DuckdbConnectionBody {
  name: string;
  kind: "duckdb";
  path: string;
}

export type CreateConnectionBody = PostgresConnectionBody | DuckdbConnectionBody;

export interface ConnectionProbe {
  reachable: boolean;
  read_only: boolean;
  server_version: string | null;
  error: string | null;
}

export interface IntrospectionSchema {
  name: string;
  tables: unknown[];
}

export interface Introspection {
  schemas: IntrospectionSchema[];
}

export interface CatalogColumn {
  id: string;
  name: string;
  type: string;
  nullable: boolean;
  selected: boolean;
  description: string | null;
}

export interface CatalogTable {
  id: string;
  schema: string;
  name: string;
  selected: boolean;
  description: string | null;
  row_estimate: number | null;
  columns: CatalogColumn[];
}

export interface CatalogPatch {
  selected?: boolean;
  description?: string;
}

export type IndexState = "idle" | "running" | "done" | "failed";

export interface IndexStatus {
  status: IndexState;
  indexed_tables: number;
  indexed_columns: number;
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorBody {
  code: string;
  message: string;
}

interface Envelope {
  success: boolean;
  data: unknown;
  error: ErrorBody | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isEnvelope(value: unknown): value is Envelope {
  return isRecord(value) && typeof value.success === "boolean";
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    isRecord(value) && typeof value.code === "string" && typeof value.message === "string"
  );
}

function invalidResponse(status: number): ApiError {
  return new ApiError(
    "invalid_response",
    "The server returned an unexpected response.",
    status,
  );
}

async function request<T>(path: string, method: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError("network_error", "Could not reach the server.", 0);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw invalidResponse(response.status);
  }
  if (!isEnvelope(payload)) throw invalidResponse(response.status);

  if (!payload.success) {
    if (isErrorBody(payload.error)) {
      throw new ApiError(payload.error.code, payload.error.message, response.status);
    }
    throw invalidResponse(response.status);
  }
  if (!response.ok) throw invalidResponse(response.status);
  return payload.data as T;
}

const segment = encodeURIComponent;

export function listConnections(): Promise<Connection[]> {
  return request("/api/v1/connections", "GET");
}

export function createConnection(body: CreateConnectionBody): Promise<Connection> {
  return request("/api/v1/connections", "POST", body);
}

export function deleteConnection(id: string): Promise<{ id: string; deleted: boolean }> {
  return request(`/api/v1/connections/${segment(id)}`, "DELETE");
}

export function testConnection(id: string): Promise<ConnectionProbe> {
  return request(`/api/v1/connections/${segment(id)}/test`, "POST");
}

export function introspect(id: string): Promise<Introspection> {
  return request(`/api/v1/connections/${segment(id)}/introspect`, "GET");
}

export function importCatalog(connectionId: string): Promise<unknown> {
  return request(`/api/v1/catalog/${segment(connectionId)}/import`, "POST");
}

export function listCatalogTables(connectionId: string): Promise<CatalogTable[]> {
  return request(`/api/v1/catalog/${segment(connectionId)}/tables`, "GET");
}

export function updateTable(id: string, patch: CatalogPatch): Promise<unknown> {
  return request(`/api/v1/catalog/tables/${segment(id)}`, "PATCH", patch);
}

export function updateColumn(id: string, patch: CatalogPatch): Promise<unknown> {
  return request(`/api/v1/catalog/columns/${segment(id)}`, "PATCH", patch);
}

export function buildIndex(connectionId: string): Promise<IndexStatus> {
  return request(`/api/v1/catalog/${segment(connectionId)}/index`, "POST");
}

export function indexStatus(connectionId: string): Promise<IndexStatus> {
  return request(`/api/v1/catalog/${segment(connectionId)}/index`, "GET");
}
