import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConnectionsPage } from "@/components/connections/ConnectionsPage";
import { ToastProvider } from "@/components/ui/Toast";
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
const OK_PROBE = {
  reachable: true,
  read_only: true,
  server_version: "16.2",
  error: null,
};

function renderPage() {
  return render(
    <ToastProvider>
      <ConnectionsPage />
    </ToastProvider>,
  );
}

function card(name: string): HTMLElement {
  const item = screen
    .getAllByRole("listitem")
    .find((li) => within(li).queryByRole("heading", { name }));
  if (!item) throw new Error(`no card for ${name}`);
  return item;
}

async function openDialog() {
  await userEvent.click(
    screen.getAllByRole("button", { name: "Add connection" })[0] as HTMLElement,
  );
  return screen.getByRole("dialog", { name: "Add connection" });
}

async function fillPostgres(dialog: HTMLElement) {
  const values: Record<string, string> = {
    Name: "Warehouse 2",
    Host: "db2.example.internal",
    Database: "analytics",
    User: "reader",
    Password: PASSWORD,
  };
  for (const [label, value] of Object.entries(values)) {
    await userEvent.type(within(dialog).getByLabelText(label), value);
  }
}

beforeEach(() => {
  vi.resetAllMocks();
  api.listConnections.mockResolvedValue([postgresConnection, duckdbConnection]);
  api.deleteConnection.mockResolvedValue({ id: "x", deleted: true });
  api.createConnection.mockResolvedValue({ ...postgresConnection, id: "new-1" });
  api.testConnection.mockResolvedValue(OK_PROBE);
});

describe("ConnectionsPage cards", () => {
  it("lists connections with kind, target and an unverified status", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    expect(
      within(card("Warehouse")).getByText(/db\.example\.internal:5432 \/ warehouse/),
    ).toBeVisible();
    expect(within(card("Local demo")).getByText(/\/data\/demo\.duckdb/)).toBeVisible();
    expect(within(card("Warehouse")).getByText("Not verified")).toBeVisible();
    expect(
      within(card("Warehouse")).getByRole("link", { name: "Open catalog" }),
    ).toHaveAttribute("href", `/catalog?connection=${postgresConnection.id}`);
  });

  it("guides the user when there are no connections", async () => {
    api.listConnections.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("No connections yet")).toBeVisible();
  });

  it("shows API load errors", async () => {
    api.listConnections.mockRejectedValue(
      new api.ApiError("x", "Could not reach the server."),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the server.",
    );
  });

  it("tests a connection and shows the read-only badge and server version", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    await userEvent.click(
      within(card("Warehouse")).getByRole("button", { name: "Test Warehouse" }),
    );
    expect(
      await within(card("Warehouse")).findByText("Read-only verified"),
    ).toBeVisible();
    expect(within(card("Warehouse")).getByText("Server 16.2")).toBeVisible();
    expect(api.testConnection).toHaveBeenCalledWith(postgresConnection.id);
  });

  it("shows why a test failed and warns about writable accounts", async () => {
    api.testConnection
      .mockResolvedValueOnce({
        reachable: false,
        read_only: false,
        server_version: null,
        error: "password authentication failed",
      })
      .mockResolvedValueOnce({
        reachable: true,
        read_only: false,
        server_version: null,
        error: null,
      });
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    await userEvent.click(
      within(card("Warehouse")).getByRole("button", { name: "Test Warehouse" }),
    );
    expect(await within(card("Warehouse")).findByText("Test failed")).toBeVisible();
    expect(
      within(card("Warehouse")).getByText("password authentication failed"),
    ).toBeVisible();
    await userEvent.click(
      within(card("Local demo")).getByRole("button", { name: "Test Local demo" }),
    );
    expect(await within(card("Local demo")).findByText("Not read-only")).toBeVisible();
  });

  it("reports a failed test request without a stuck spinner", async () => {
    api.testConnection.mockRejectedValue(new api.ApiError("x", "Probe timed out"));
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    await userEvent.click(
      within(card("Warehouse")).getByRole("button", { name: "Test Warehouse" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Probe timed out");
    expect(within(card("Warehouse")).getByText("Not verified")).toBeVisible();
  });
});

describe("deleting", () => {
  it("asks in-app, then deletes and reloads", async () => {
    const confirm = vi.spyOn(window, "confirm");
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    api.listConnections.mockResolvedValue([duckdbConnection]);

    await userEvent.click(
      screen.getByRole("button", { name: "More actions for Warehouse" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete connection?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.deleteConnection).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "More actions for Warehouse" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() =>
      expect(api.deleteConnection).toHaveBeenCalledWith(postgresConnection.id),
    );
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Warehouse" })).toBeNull(),
    );
    expect(confirm).not.toHaveBeenCalled();
  });

  it("shows the API error when deleting fails", async () => {
    api.deleteConnection.mockRejectedValue(new api.ApiError("x", "Connection is in use"));
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    await userEvent.click(
      screen.getByRole("button", { name: "More actions for Warehouse" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection is in use");
  });
});

describe("Add connection dialog", () => {
  it("validates required fields inline and does not call the API", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));
    expect(within(dialog).getByText("Name is required")).toBeVisible();
    expect(within(dialog).getByText("Host is required")).toBeVisible();
    expect(within(dialog).getByLabelText("Host")).toHaveAttribute("aria-invalid", "true");
    expect(api.createConnection).not.toHaveBeenCalled();
  });

  it("switches fields with the Postgres | DuckDB toggle", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    expect(within(dialog).getByLabelText("Host")).toBeVisible();
    await userEvent.click(within(dialog).getByLabelText("DuckDB"));
    expect(within(dialog).queryByLabelText("Host")).toBeNull();
    expect(within(dialog).getByLabelText("Path")).toBeVisible();
  });

  it("tests before saving, then keeps the tested draft without creating twice", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    await fillPostgres(dialog);

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Test connection" }),
    );
    expect(await within(dialog).findByText(/Read-only access verified/)).toBeVisible();
    expect(api.createConnection).toHaveBeenCalledTimes(1);
    expect(api.createConnection).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "postgres",
        host: "db2.example.internal",
        port: 5432,
        password: PASSWORD,
      }),
    );

    await userEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.createConnection).toHaveBeenCalledTimes(1);
    expect(api.deleteConnection).not.toHaveBeenCalled();
    expect(await screen.findByText("Connection added")).toBeVisible();
    expect(api.listConnections).toHaveBeenCalledTimes(2);
  });

  it("removes the draft when the test fails and shows the reason", async () => {
    api.testConnection.mockResolvedValue({
      reachable: false,
      read_only: false,
      server_version: null,
      error: "connection refused",
    });
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    await fillPostgres(dialog);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Test connection" }),
    );
    expect(await within(dialog).findByText("connection refused")).toBeVisible();
    expect(api.deleteConnection).toHaveBeenCalledWith("new-1");
  });

  it("discards the tested draft when the form is edited or cancelled", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    await fillPostgres(dialog);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Test connection" }),
    );
    await within(dialog).findByText(/Read-only access verified/);

    await userEvent.type(within(dialog).getByLabelText("Name"), "!");
    expect(api.deleteConnection).toHaveBeenCalledWith("new-1");
    expect(within(dialog).queryByText(/Read-only access verified/)).toBeNull();

    api.deleteConnection.mockClear();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Test connection" }),
    );
    await within(dialog).findByText(/Read-only access verified/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.deleteConnection).toHaveBeenCalledWith("new-1");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("saves without a prior test and shows the probe result on the new card", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    api.listConnections.mockResolvedValue([
      postgresConnection,
      duckdbConnection,
      { ...postgresConnection, id: "new-1", name: "Warehouse 2" },
    ]);
    const dialog = await openDialog();
    await fillPostgres(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));
    await screen.findByRole("heading", { name: "Warehouse 2" });
    expect(api.testConnection).toHaveBeenCalledWith("new-1");
    expect(within(card("Warehouse 2")).getByText("Read-only verified")).toBeVisible();
  });

  it("shows the API error inside the dialog and keeps the values", async () => {
    api.createConnection.mockRejectedValue(
      new api.ApiError("conflict", "A connection with that name exists"),
    );
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    const dialog = await openDialog();
    await fillPostgres(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add connection" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "A connection with that name exists",
    );
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Warehouse 2");
  });

  it("closes on Escape", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Warehouse" });
    await openDialog();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
