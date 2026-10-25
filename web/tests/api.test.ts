import {
  ApiError,
  buildIndex,
  createConnection,
  deleteConnection,
  importCatalog,
  indexStatus,
  introspect,
  listCatalogTables,
  listConnections,
  testConnection,
  updateColumn,
  updateTable,
} from "@/lib/api";
import { API_URL } from "@/lib/config";
import { catalogTables, duckdbConnection, postgresConnection } from "./fixtures/admin";

const PASSWORD = "SENTINEL-pw-'; DROP TABLE x;--"; // gitleaks:allow

function envelope(data: unknown, meta: unknown = null) {
  return { success: true, data, error: null, meta };
}

function failure(code: string, message: string) {
  return { success: false, data: null, error: { code, message }, meta: null };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function lastCall(): { url: string; method: string; body: unknown } {
    const call = fetchMock.mock.calls.at(-1) as [string, RequestInit | undefined];
    const [url, init] = call;
    const rawBody = init?.body;
    return {
      url,
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof rawBody === "string" ? JSON.parse(rawBody) : rawBody,
    };
  }

  describe("connections", () => {
    it("listConnections GETs the collection and unwraps the envelope", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(envelope([postgresConnection, duckdbConnection], { total: 2 })),
      );

      const result = await listConnections();

      expect(result).toEqual([postgresConnection, duckdbConnection]);
      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/connections`,
        method: "GET",
      });
    });

    it("listConnections returns an empty array for no connections", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope([])));
      await expect(listConnections()).resolves.toEqual([]);
    });

    it("createConnection POSTs the JSON body for postgres", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(postgresConnection), 201));
      const body = {
        name: "Warehouse",
        kind: "postgres" as const,
        host: "db.example.internal",
        port: 5432,
        database: "warehouse",
        user: "reader",
        password: PASSWORD,
        sslmode: "prefer" as const,
      };

      const result = await createConnection(body);

      expect(result).toEqual(postgresConnection);
      const call = lastCall();
      expect(call.url).toBe(`${API_URL}/api/v1/connections`);
      expect(call.method).toBe("POST");
      expect(call.body).toEqual(body);
      const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
      expect(new Headers(init.headers).get("Content-Type")).toMatch(/application\/json/);
    });

    it("createConnection POSTs the JSON body for duckdb", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(duckdbConnection), 201));
      const body = { name: "Local", kind: "duckdb" as const, path: "/data/local.duckdb" };

      await createConnection(body);

      expect(lastCall().body).toEqual(body);
    });

    it("deleteConnection DELETEs the encoded id", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(envelope({ id: "c-1", deleted: true })),
      );

      await deleteConnection("../c 1");

      const call = lastCall();
      expect(call.method).toBe("DELETE");
      expect(call.url).toBe(`${API_URL}/api/v1/connections/..%2Fc%201`);
    });

    it("testConnection POSTs to /test and returns the probe outcome", async () => {
      const probe = {
        reachable: true,
        read_only: true,
        server_version: "16.4",
        error: null,
      };
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(probe)));

      await expect(testConnection(postgresConnection.id)).resolves.toEqual(probe);

      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/connections/${postgresConnection.id}/test`,
        method: "POST",
      });
    });

    it("introspect GETs the connection's introspection", async () => {
      const schemas = { schemas: [{ name: "public", tables: [] }] };
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(schemas)));

      await expect(introspect(postgresConnection.id)).resolves.toEqual(schemas);

      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/connections/${postgresConnection.id}/introspect`,
        method: "GET",
      });
    });
  });

  describe("catalog", () => {
    const connectionId = postgresConnection.id;

    it("importCatalog POSTs to the import endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope({ tables: 2 })));

      await importCatalog(connectionId);

      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/catalog/${connectionId}/import`,
        method: "POST",
      });
    });

    it("listCatalogTables GETs tables with their columns", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(catalogTables)));

      await expect(listCatalogTables(connectionId)).resolves.toEqual(catalogTables);

      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/catalog/${connectionId}/tables`,
        method: "GET",
      });
    });

    it("updateTable PATCHes only the given fields", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope(catalogTables[0])));

      await updateTable("t-1", { selected: false });

      expect(lastCall()).toEqual({
        url: `${API_URL}/api/v1/catalog/tables/t-1`,
        method: "PATCH",
        body: { selected: false },
      });
    });

    it("updateColumn PATCHes a description verbatim (unicode and SQL metacharacters)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(envelope({})));
      const description = "Status 🚚 'shipped'; -- not \"NULL\"";

      await updateColumn("col/1", { description });

      expect(lastCall()).toEqual({
        url: `${API_URL}/api/v1/catalog/columns/col%2F1`,
        method: "PATCH",
        body: { description },
      });
    });

    it("buildIndex POSTs and indexStatus GETs the index endpoint", async () => {
      const running = { status: "running", indexed_tables: 0, indexed_columns: 0 };
      const done = { status: "done", indexed_tables: 2, indexed_columns: 5 };
      fetchMock
        .mockResolvedValueOnce(jsonResponse(envelope(running)))
        .mockResolvedValueOnce(jsonResponse(envelope(done)));

      await expect(buildIndex(connectionId)).resolves.toEqual(running);
      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/catalog/${connectionId}/index`,
        method: "POST",
      });

      await expect(indexStatus(connectionId)).resolves.toEqual(done);
      expect(lastCall()).toMatchObject({
        url: `${API_URL}/api/v1/catalog/${connectionId}/index`,
        method: "GET",
      });
    });
  });

  describe("errors", () => {
    it("throws ApiError with the envelope code and message on a non-2xx response", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(failure("not_found", "Connection not found"), 404),
      );

      const error = await deleteConnection("missing").catch((e: unknown) => e);

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toBeInstanceOf(Error);
      expect(error).toMatchObject({
        code: "not_found",
        message: "Connection not found",
        status: 404,
      });
    });

    it("throws ApiError when a 2xx response carries success: false", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(failure("conflict", "Already exists"), 200),
      );

      await expect(listConnections()).rejects.toMatchObject({
        code: "conflict",
        message: "Already exists",
      });
    });

    it("throws ApiError with code invalid_response when the body is not JSON", async () => {
      fetchMock.mockResolvedValueOnce(
        new Response("<html>Bad Gateway</html>", { status: 502 }),
      );

      const error = await listConnections().catch((e: unknown) => e);

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: "invalid_response", status: 502 });
      expect((error as Error).message).not.toContain("<html>");
    });

    it("throws ApiError with code invalid_response when the JSON is not an envelope", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not Found" }, 404));

      await expect(listConnections()).rejects.toMatchObject({
        code: "invalid_response",
        status: 404,
      });
    });

    it("throws ApiError with code network_error when fetch rejects", async () => {
      fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

      const error = await listConnections().catch((e: unknown) => e);

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: "network_error", status: 0 });
    });

    it("never includes the request body (password) in error messages", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(
          failure("validation_error", "port must be between 1 and 65535"),
          422,
        ),
      );

      const error = await createConnection({
        name: "x",
        kind: "postgres",
        host: "h",
        port: 70000,
        database: "d",
        user: "u",
        password: PASSWORD,
        sslmode: "prefer",
      }).catch((e: unknown) => e);

      expect(error).toBeInstanceOf(ApiError);
      const serialized = `${(error as Error).message} ${String(error)} ${JSON.stringify(error)}`;
      expect(serialized).not.toContain("SENTINEL-pw");
    });

    it("never includes the request body when the network fails", async () => {
      fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

      const error = await createConnection({
        name: "x",
        kind: "postgres",
        host: "h",
        port: 5432,
        database: "d",
        user: "u",
        password: PASSWORD,
        sslmode: "prefer",
      }).catch((e: unknown) => e);

      const serialized = `${(error as Error).message} ${String(error)} ${JSON.stringify(error)}`;
      expect(serialized).not.toContain("SENTINEL-pw");
    });
  });
});
