import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CatalogPage } from "@/components/CatalogPage";
import { duckdbConnection, freshTables, postgresConnection } from "./fixtures/admin";

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
    listCatalogTables: vi.fn(),
    importCatalog: vi.fn(),
    updateTable: vi.fn(),
    updateColumn: vi.fn(),
    buildIndex: vi.fn(),
    indexStatus: vi.fn(),
  };
});

vi.mock("@/lib/api", () => api);

const IDLE = { status: "idle", indexed_tables: 0, indexed_columns: 0 };
const POLL_STEP_MS = 2000;

function never<T>(): Promise<T> {
  return new Promise<T>(() => undefined);
}

function tableItem(qualifiedName: string): HTMLElement {
  const item = screen.getByRole("checkbox", { name: qualifiedName }).closest("li");
  if (!item) throw new Error(`no list item for ${qualifiedName}`);
  return item;
}

async function renderLoaded() {
  const view = render(<CatalogPage />);
  await screen.findByRole("checkbox", { name: "public.orders" });
  return view;
}

async function expandColumns(qualifiedName: string) {
  const toggle = within(tableItem(qualifiedName)).getByRole("button", {
    name: /columns/i,
  });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
}

beforeEach(() => {
  vi.resetAllMocks();
  api.listConnections.mockResolvedValue([postgresConnection, duckdbConnection]);
  api.listCatalogTables.mockImplementation(async () => freshTables());
  api.indexStatus.mockResolvedValue(IDLE);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("CatalogPage connection picker", () => {
  it("selects the first connection and loads its tables", async () => {
    await renderLoaded();

    const picker = screen.getByLabelText("Connection");
    expect(picker).toHaveValue(postgresConnection.id);
    expect(within(picker).getByRole("option", { name: "Warehouse" })).toBeInTheDocument();
    expect(
      within(picker).getByRole("option", { name: "Local demo" }),
    ).toBeInTheDocument();
    expect(api.listCatalogTables).toHaveBeenCalledWith(postgresConnection.id);
  });

  it("loads the tables of the newly picked connection", async () => {
    await renderLoaded();
    api.listCatalogTables.mockResolvedValue([]);

    await userEvent.selectOptions(
      screen.getByLabelText("Connection"),
      duckdbConnection.id,
    );

    await waitFor(() =>
      expect(api.listCatalogTables).toHaveBeenLastCalledWith(duckdbConnection.id),
    );
    expect(await screen.findByText(/no tables imported/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "public.orders" })).toBeNull();
  });

  it("links to the connections page when no connection exists", async () => {
    api.listConnections.mockResolvedValue([]);
    render(<CatalogPage />);

    const link = await screen.findByRole("link", { name: /connection/i });
    expect(link).toHaveAttribute("href", "/connections");
    expect(api.listCatalogTables).not.toHaveBeenCalled();
  });

  it("announces loading while tables are fetched", async () => {
    api.listCatalogTables.mockReturnValue(never());
    render(<CatalogPage />);
    await waitFor(() => expect(api.listCatalogTables).toHaveBeenCalled());
    const statuses = screen.getAllByRole("status").map((el) => el.textContent ?? "");
    expect(statuses.some((text) => /loading/i.test(text))).toBe(true);
  });

  it("shows an alert when tables cannot be loaded", async () => {
    api.listCatalogTables.mockRejectedValue(
      new api.ApiError("not_found", "Connection not found", 404),
    );
    render(<CatalogPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection not found");
  });
});

describe("CatalogPage tables and columns", () => {
  it("lists tables with a selection checkbox and description", async () => {
    await renderLoaded();

    expect(screen.getByRole("list", { name: "Tables" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "public.orders" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "analytics.events" })).not.toBeChecked();
    expect(screen.getByLabelText("Description for public.orders")).toHaveValue(
      "Customer orders",
    );
    expect(screen.getByLabelText("Description for analytics.events")).toHaveValue("");
  });

  it("expands a table to show its columns with checkboxes", async () => {
    await renderLoaded();
    expect(screen.queryByRole("checkbox", { name: "public.orders.id" })).toBeNull();

    await expandColumns("public.orders");

    expect(screen.getByRole("checkbox", { name: "public.orders.id" })).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "public.orders.status" }),
    ).not.toBeChecked();
    expect(screen.getByLabelText("Description for public.orders.id")).toHaveValue(
      "Primary key",
    );
    expect(within(tableItem("public.orders")).getByText(/integer/)).toBeInTheDocument();
  });

  it("toggling a table PATCHes only selected", async () => {
    api.updateTable.mockResolvedValue({});
    await renderLoaded();

    await userEvent.click(screen.getByRole("checkbox", { name: "analytics.events" }));

    expect(api.updateTable).toHaveBeenCalledWith("t-events", { selected: true });
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "analytics.events" })).toBeChecked(),
    );
  });

  it("reverts the checkbox and alerts when the table update fails", async () => {
    api.updateTable.mockRejectedValue(
      new api.ApiError("conflict", "Catalog is locked", 409),
    );
    await renderLoaded();

    await userEvent.click(screen.getByRole("checkbox", { name: "public.orders" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Catalog is locked");
    expect(screen.getByRole("checkbox", { name: "public.orders" })).toBeChecked();
  });

  it("toggling a column PATCHes only selected", async () => {
    api.updateColumn.mockResolvedValue({});
    await renderLoaded();
    await expandColumns("public.orders");

    await userEvent.click(screen.getByRole("checkbox", { name: "public.orders.status" }));

    expect(api.updateColumn).toHaveBeenCalledWith("c-orders-status", { selected: true });
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "public.orders.status" }),
      ).toBeChecked(),
    );
  });

  it("saves a table description on blur with only the description field", async () => {
    api.updateTable.mockResolvedValue({});
    await renderLoaded();
    const field = screen.getByLabelText("Description for public.orders");
    const text = "Orders 🧾 'paid'; DROP TABLE orders;-- <b>x</b>";

    await userEvent.clear(field);
    await userEvent.type(field, text);
    await userEvent.tab();

    await waitFor(() => expect(api.updateTable).toHaveBeenCalledTimes(1));
    expect(api.updateTable).toHaveBeenCalledWith("t-orders", { description: text });
  });

  it("does not save a description that did not change", async () => {
    await renderLoaded();
    await userEvent.click(screen.getByLabelText("Description for public.orders"));
    await userEvent.tab();
    expect(api.updateTable).not.toHaveBeenCalled();
  });

  it("saves a column description on blur", async () => {
    api.updateColumn.mockResolvedValue({});
    await renderLoaded();
    await expandColumns("public.orders");
    const field = screen.getByLabelText("Description for public.orders.status");

    await userEvent.type(field, "Order lifecycle state");
    await userEvent.tab();

    await waitFor(() =>
      expect(api.updateColumn).toHaveBeenCalledWith("c-orders-status", {
        description: "Order lifecycle state",
      }),
    );
    expect(api.updateColumn).toHaveBeenCalledTimes(1);
  });

  it("warns when no tables are selected", async () => {
    api.updateTable.mockResolvedValue({});
    await renderLoaded();
    expect(screen.queryByText(/no tables (are )?selected/i)).toBeNull();

    await userEvent.click(screen.getByRole("checkbox", { name: "public.orders" }));

    expect(await screen.findByText(/no tables (are )?selected/i)).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been imported", async () => {
    api.listCatalogTables.mockResolvedValue([]);
    render(<CatalogPage />);
    expect(await screen.findByText(/no tables imported/i)).toBeInTheDocument();
  });
});

describe("CatalogPage import", () => {
  it("imports the schema, then reloads the tables", async () => {
    api.importCatalog.mockResolvedValue({});
    await renderLoaded();
    expect(api.listCatalogTables).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "Import schema" }));

    expect(api.importCatalog).toHaveBeenCalledWith(postgresConnection.id);
    await waitFor(() => expect(api.listCatalogTables).toHaveBeenCalledTimes(2));
  });

  it("disables the import button and announces progress while importing", async () => {
    api.importCatalog.mockReturnValue(never());
    await renderLoaded();

    await userEvent.click(screen.getByRole("button", { name: "Import schema" }));

    expect(screen.getByRole("button", { name: "Import schema" })).toBeDisabled();
    expect(screen.getByText(/importing/i)).toBeInTheDocument();
  });

  it("alerts when the import fails", async () => {
    api.importCatalog.mockRejectedValue(
      new api.ApiError("unreachable", "Could not reach the database", 502),
    );
    await renderLoaded();

    await userEvent.click(screen.getByRole("button", { name: "Import schema" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the database",
    );
  });
});

describe("CatalogPage index build", () => {
  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function setupFakeTimers() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  }

  function indexRegion() {
    return screen.getByRole("status", { name: "Index status" });
  }

  it("builds the index and polls its status until done", async () => {
    const user = setupFakeTimers();
    let server: unknown = IDLE;
    api.indexStatus.mockImplementation(async () => server);
    api.buildIndex.mockImplementation(async () => {
      server = { status: "running", indexed_tables: 0, indexed_columns: 0 };
      return server;
    });
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Build index" }));

    expect(api.buildIndex).toHaveBeenCalledWith(postgresConnection.id);
    await waitFor(() => expect(indexRegion()).toHaveTextContent(/running/i));
    expect(screen.getByRole("button", { name: "Build index" })).toBeDisabled();

    server = { status: "done", indexed_tables: 2, indexed_columns: 5 };
    await advance(POLL_STEP_MS);

    await waitFor(() => expect(indexRegion()).toHaveTextContent(/done/i));
    expect(indexRegion()).toHaveTextContent(/2 tables/i);
    expect(indexRegion()).toHaveTextContent(/5 columns/i);
    expect(api.indexStatus).toHaveBeenCalledWith(postgresConnection.id);

    const callsWhenDone = api.indexStatus.mock.calls.length;
    await advance(POLL_STEP_MS * 5);
    expect(api.indexStatus).toHaveBeenCalledTimes(callsWhenDone);
    expect(screen.getByRole("button", { name: "Build index" })).toBeEnabled();
  });

  it("stops polling and shows failure when the build fails", async () => {
    const user = setupFakeTimers();
    let server: unknown = IDLE;
    api.indexStatus.mockImplementation(async () => server);
    api.buildIndex.mockImplementation(async () => {
      server = { status: "running", indexed_tables: 0, indexed_columns: 0 };
      return server;
    });
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Build index" }));
    server = { status: "failed", indexed_tables: 1, indexed_columns: 2 };
    await advance(POLL_STEP_MS);

    await waitFor(() => expect(indexRegion()).toHaveTextContent(/failed/i));
    const callsWhenFailed = api.indexStatus.mock.calls.length;
    await advance(POLL_STEP_MS * 5);
    expect(api.indexStatus).toHaveBeenCalledTimes(callsWhenFailed);
  });

  it("stops polling and alerts when a status request errors", async () => {
    const user = setupFakeTimers();
    api.buildIndex.mockResolvedValue({
      status: "running",
      indexed_tables: 0,
      indexed_columns: 0,
    });
    await renderLoaded();
    api.indexStatus.mockRejectedValue(
      new api.ApiError("db_unavailable", "Index status unavailable", 503),
    );

    await user.click(screen.getByRole("button", { name: "Build index" }));
    await advance(POLL_STEP_MS);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Index status unavailable",
    );
    const callsAfterError = api.indexStatus.mock.calls.length;
    await advance(POLL_STEP_MS * 5);
    expect(api.indexStatus).toHaveBeenCalledTimes(callsAfterError);
  });

  it("stops polling when the page unmounts", async () => {
    const user = setupFakeTimers();
    const running = { status: "running", indexed_tables: 0, indexed_columns: 0 };
    api.buildIndex.mockResolvedValue(running);
    const view = await renderLoaded();
    api.indexStatus.mockResolvedValue(running);

    await user.click(screen.getByRole("button", { name: "Build index" }));
    await advance(POLL_STEP_MS);
    view.unmount();
    const callsAtUnmount = api.indexStatus.mock.calls.length;

    await advance(POLL_STEP_MS * 5);
    expect(api.indexStatus).toHaveBeenCalledTimes(callsAtUnmount);
  });

  it("alerts when starting the build fails", async () => {
    api.buildIndex.mockRejectedValue(
      new api.ApiError("no_selection", "Select at least one table first", 400),
    );
    await renderLoaded();

    await userEvent.click(screen.getByRole("button", { name: "Build index" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select at least one table first",
    );
  });
});
