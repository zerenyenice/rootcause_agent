import type { ConnectionKind, CreateConnectionBody, SslMode } from "./api";

export const SSL_MODES: readonly SslMode[] = [
  "disable",
  "prefer",
  "require",
  "verify-full",
];
export const DEFAULT_PORT = "5432";
const MIN_PORT = 1;
const MAX_PORT = 65535;

export interface ConnectionFormValues {
  name: string;
  kind: ConnectionKind;
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
  sslmode: SslMode;
  path: string;
}

export type FieldName =
  "name" | "host" | "port" | "database" | "user" | "password" | "path";
export type FormErrors = Partial<Record<FieldName, string>>;

export const EMPTY_FORM: ConnectionFormValues = {
  name: "",
  kind: "postgres",
  host: "",
  port: DEFAULT_PORT,
  database: "",
  user: "",
  password: "",
  sslmode: "prefer",
  path: "",
};

const REQUIRED_TEXT: Record<ConnectionKind, readonly FieldName[]> = {
  postgres: ["name", "host", "database", "user"],
  duckdb: ["name", "path"],
};

export const FIELD_LABELS: Record<FieldName, string> = {
  name: "Name",
  host: "Host",
  port: "Port",
  database: "Database",
  user: "User",
  password: "Password",
  path: "Path",
};

function parsePort(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const port = Number(trimmed);
  return port >= MIN_PORT && port <= MAX_PORT ? port : null;
}

export function validateConnectionForm(values: ConnectionFormValues): FormErrors {
  const errors: FormErrors = {};
  for (const field of REQUIRED_TEXT[values.kind]) {
    if (values[field].trim() === "") errors[field] = `${FIELD_LABELS[field]} is required`;
  }
  if (values.kind === "postgres") {
    if (values.password === "") errors.password = "Password is required";
    if (values.port.trim() === "") errors.port = "Port is required";
    else if (parsePort(values.port) === null) {
      errors.port = `Port must be a whole number between ${MIN_PORT} and ${MAX_PORT}`;
    }
  }
  return errors;
}

/** Build the request body from already-validated values. */
export function toCreateBody(values: ConnectionFormValues): CreateConnectionBody {
  const name = values.name.trim();
  if (values.kind === "duckdb") return { name, kind: "duckdb", path: values.path.trim() };
  return {
    name,
    kind: "postgres",
    host: values.host.trim(),
    port: parsePort(values.port) ?? Number(DEFAULT_PORT),
    database: values.database.trim(),
    user: values.user.trim(),
    password: values.password,
    sslmode: values.sslmode,
  };
}
