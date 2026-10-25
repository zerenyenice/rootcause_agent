import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConnectionsPage } from "@/components/ConnectionsPage";
import { duckdbConnection, postgresConnection } from "./fixtures/admin";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      public readonly code: string,
      message: string,
      public readonly status = 400,
    ) {
      super(message);
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    listConnections: vi.fn(),
    createConnection: vi.fn(),
    deleteConnection: vi.fn(),
    testConnection: vi.fn(),
  };
});

vi.mock("@/lib/api", () => api);

const PASSWORD = "SENTINEL-pw-123"; // gitleaks:allow

function never<T>(): Promise<T> {
  return new Promise<T>(() => undefined);
}

function connectionsList() {
  return screen.findByRole("list", { name: "Connections" });
}

async function rowFor(name: string): Promise<HTMLElement> {
  const list = await connectionsList();
  const row = within(list)
    .getAllByRole("listitem")
    .find((item) => item.textContent?.includes(name));
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

async function fillPostgres(overrides: Partial<Record<string, string>> = {}) {
  const values = {
    Name: "Warehouse 2",
    Host: "db2.example.internal",
    Database: "analytics",
    User: "reader",
    Password: PASSWORD,
    ...overrides,
  };
  for (const [label, value] of Object.entries(values)) {
    const field = screen.getByLabelText(label);
    await userEvent.clear(field);
    if (value) await userEvent.type(field, value);
  }
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
  api.listConnections.mockResolvedValue([postgresConnection, duckdbConnection]);
});

describe("ConnectionsPage list", () => {
  it("announces loading while connections are fetched", () => {
    api.listConnections.mockReturnValue(never());
    render(<ConnectionsPage />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("lists each connection with name, kind and host/database or path", async () => {
    render(<ConnectionsPage />);

    const pg = await rowFor("Warehouse");
    expect(pg).toHaveTextContent("postgres");
    expect(pg).toHaveTextContent("db.example.internal");
    expect(pg).toHaveTextContent("warehouse");

    const duck = await rowFor("Local demo");
    expect(duck).toHaveTextContent("duckdb");
    expect(duck).toHaveTextContent("/data/demo.duckdb");
  });

  it("shows an empty state when there are no connections", async () => {
    api.listConnections.mockResolvedValue([]);
    render(<ConnectionsPage />);
    expect(await screen.findByText(/no connections/i)).toBeInTheDocument();
  });

  it("shows the API error message in an alert when loading fails", async () => {
    api.listConnections.mockRejectedValue(
      new api.ApiError("db_unavailable", "The catalog database is unavailable", 503),
    );
    render(<ConnectionsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The catalog database is unavailable",
    );
  });

  it("shows a generic message, not raw details, for unexpected errors", async () => {
    api.listConnections.mockRejectedValue(
      new Error("TypeError at line 42 secret-detail"),
    );
    render(<ConnectionsPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong/i);
    expect(alert).not.toHaveTextContent("secret-detail");
  });
});

describe("ConnectionsPage add form", () => {
  it("defaults to postgres with port 5432, a password input and sslmode prefer", async () => {
    render(<ConnectionsPage />);
    await connectionsList();

    expect(screen.getByRole("form", { name: "Add connection" })).toBeInTheDocument();
    expect(screen.getByLabelText("Kind")).toHaveValue("postgres");
    expect(screen.getByLabelText("Port")).toHaveValue(5432);
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    const sslmode = screen.getByLabelText("SSL mode");
    expect(sslmode).toHaveValue("prefer");
    const options = within(sslmode)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual(["disable", "prefer", "require", "verify-full"]);
    expect(screen.queryByLabelText("Path")).toBeNull();
  });

  it("switches to a path field for duckdb", async () => {
    render(<ConnectionsPage />);
    await connectionsList();

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "duckdb");

    expect(screen.getByLabelText("Path")).toBeInTheDocument();
    for (const label of ["Host", "Port", "Database", "User", "Password", "SSL mode"]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
  });

  it("flags every missing required field accessibly and does not submit", async () => {
    render(<ConnectionsPage />);
    await connectionsList();

    await userEvent.type(screen.getByLabelText("Name"), "   ");
    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    for (const label of ["Name", "Host", "Database", "User", "Password"]) {
      const field = screen.getByLabelText(label);
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(field).toHaveAccessibleDescription(/required/i);
    }
    expect(api.createConnection).not.toHaveBeenCalled();
  });

  it("requires a path for duckdb", async () => {
    render(<ConnectionsPage />);
    await connectionsList();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "duckdb");
    await userEvent.type(screen.getByLabelText("Name"), "Local");

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    expect(screen.getByLabelText("Path")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Path")).toHaveAccessibleDescription(/required/i);
    expect(api.createConnection).not.toHaveBeenCalled();
  });

  it.each(["0", "65536", "70000", ""])("rejects port %j", async (port) => {
    render(<ConnectionsPage />);
    await connectionsList();
    await fillPostgres();
    await userEvent.clear(screen.getByLabelText("Port"));
    if (port) await userEvent.type(screen.getByLabelText("Port"), port);

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    const field = screen.getByLabelText("Port");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription(/1.*65535|required/i);
    expect(api.createConnection).not.toHaveBeenCalled();
  });

  it.each([1, 65535])("accepts boundary port %d", async (port) => {
    api.createConnection.mockResolvedValue(postgresConnection);
    render(<ConnectionsPage />);
    await connectionsList();
    await fillPostgres();
    await userEvent.clear(screen.getByLabelText("Port"));
    await userEvent.type(screen.getByLabelText("Port"), String(port));

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    await waitFor(() => expect(api.createConnection).toHaveBeenCalledTimes(1));
    expect(api.createConnection.mock.calls[0]?.[0]).toMatchObject({ port });
  });

  it("creates a postgres connection, refreshes the list and clears the password", async () => {
    const created = {
      ...postgresConnection,
      id: "33333333-3333-4333-8333-333333333333",
      name: "Warehouse 2",
    };
    api.createConnection.mockResolvedValue(created);
    render(<ConnectionsPage />);
    await connectionsList();
    api.listConnections.mockResolvedValue([
      postgresConnection,
      duckdbConnection,
      created,
    ]);
    await fillPostgres();
    await userEvent.selectOptions(screen.getByLabelText("SSL mode"), "require");

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    await waitFor(() => expect(api.createConnection).toHaveBeenCalledTimes(1));
    expect(api.createConnection).toHaveBeenCalledWith({
      name: "Warehouse 2",
      kind: "postgres",
      host: "db2.example.internal",
      port: 5432,
      database: "analytics",
      user: "reader",
      password: PASSWORD,
      sslmode: "require",
    });
    expect(await rowFor("Warehouse 2")).toBeInTheDocument();
    expect(api.listConnections).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByDisplayValue(PASSWORD)).toBeNull();
    expect(document.body.textContent).not.toContain(PASSWORD);
  });

  it("creates a duckdb connection with only name, kind and path", async () => {
    api.createConnection.mockResolvedValue(duckdbConnection);
    render(<ConnectionsPage />);
    await connectionsList();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "duckdb");
    await userEvent.type(screen.getByLabelText("Name"), "Local 2");
    await userEvent.type(screen.getByLabelText("Path"), "/data/local2.duckdb");

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    await waitFor(() =>
      expect(api.createConnection).toHaveBeenCalledWith({
        name: "Local 2",
        kind: "duckdb",
        path: "/data/local2.duckdb",
      }),
    );
  });

  it("disables the submit button while the connection is being created", async () => {
    api.createConnection.mockReturnValue(never());
    render(<ConnectionsPage />);
    await connectionsList();
    await fillPostgres();

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    expect(screen.getByRole("button", { name: "Add connection" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));
    expect(api.createConnection).toHaveBeenCalledTimes(1);
  });

  it("shows the API error in an alert without echoing the password", async () => {
    api.createConnection.mockRejectedValue(
      new api.ApiError(
        "validation_error",
        "A connection with this name already exists",
        409,
      ),
    );
    render(<ConnectionsPage />);
    await connectionsList();
    await fillPostgres();

    await userEvent.click(screen.getByRole("button", { name: "Add connection" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("A connection with this name already exists");
    expect(alert).not.toHaveTextContent(PASSWORD);
    expect(api.listConnections).toHaveBeenCalledTimes(1);
  });
});

describe("ConnectionsPage row actions", () => {
  it.each([
    [
      { reachable: true, read_only: true, server_version: "16.4", error: null },
      "Read-only verified",
    ],
    [
      { reachable: true, read_only: false, server_version: "16.4", error: null },
      "Not read-only",
    ],
    [
      { reachable: false, read_only: false, server_version: null, error: "timeout" },
      "Unreachable",
    ],
  ])("tests a connection and shows its status badge (%o)", async (probe, badge) => {
    api.testConnection.mockResolvedValue(probe);
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");

    await userEvent.click(within(row).getByRole("button", { name: /^test/i }));

    expect(api.testConnection).toHaveBeenCalledWith(postgresConnection.id);
    expect(await within(row).findByText(badge)).toBeInTheDocument();
    const others = ["Read-only verified", "Not read-only", "Unreachable"].filter(
      (t) => t !== badge,
    );
    for (const other of others) expect(within(row).queryByText(other)).toBeNull();
  });

  it("shows the server version once read-only is verified", async () => {
    api.testConnection.mockResolvedValue({
      reachable: true,
      read_only: true,
      server_version: "16.4",
      error: null,
    });
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");
    await userEvent.click(within(row).getByRole("button", { name: /^test/i }));
    expect(await within(row).findByText(/16\.4/)).toBeInTheDocument();
  });

  it("announces that a test is in progress", async () => {
    api.testConnection.mockReturnValue(never());
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");
    await userEvent.click(within(row).getByRole("button", { name: /^test/i }));
    expect(within(row).getByText(/testing/i)).toBeInTheDocument();
  });

  it("shows an alert when the test request fails", async () => {
    api.testConnection.mockRejectedValue(
      new api.ApiError("not_found", "Connection not found", 404),
    );
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");
    await userEvent.click(within(row).getByRole("button", { name: /^test/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection not found");
  });

  it("does not delete when the confirmation is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");

    await userEvent.click(within(row).getByRole("button", { name: /^delete/i }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Warehouse"));
    expect(api.deleteConnection).not.toHaveBeenCalled();
    expect(await rowFor("Warehouse")).toBeInTheDocument();
  });

  it("deletes after confirmation and refreshes the list", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.deleteConnection.mockResolvedValue({ id: postgresConnection.id, deleted: true });
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");
    api.listConnections.mockResolvedValue([duckdbConnection]);

    await userEvent.click(within(row).getByRole("button", { name: /^delete/i }));

    expect(api.deleteConnection).toHaveBeenCalledWith(postgresConnection.id);
    await waitFor(async () => {
      const rows = within(await connectionsList()).getAllByRole("listitem");
      expect(rows).toHaveLength(1);
      expect(rows[0]).toHaveTextContent("Local demo");
    });
    expect(api.listConnections).toHaveBeenCalledTimes(2);
  });

  it("keeps the row and shows an alert when deletion fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.deleteConnection.mockRejectedValue(
      new api.ApiError("conflict", "Connection is in use", 409),
    );
    render(<ConnectionsPage />);
    const row = await rowFor("Warehouse");

    await userEvent.click(within(row).getByRole("button", { name: /^delete/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Connection is in use");
    expect(await rowFor("Warehouse")).toBeInTheDocument();
  });
});
