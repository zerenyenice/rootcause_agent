import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CatalogPage } from "@/components/catalog/CatalogPage";
import { ToastProvider } from "@/components/ui/Toast";
import { duckdbConnection, freshTables, postgresConnection } from "./fixtures/admin";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
}));

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

function renderPage() {
  return render(
    <ToastProvider>
      <CatalogPage />
    </ToastProvider>,
  );
}

async function renderLoaded() {
  const view = renderPage();
  await screen.findByRole("checkbox", { name: "Include public.orders" });
  return view;
}

beforeEach(() => {
  vi.resetAllMocks();
  nav.search = "";
  api.listConnections.mockResolvedValue([postgresConnection, duckdbConnection]);
  api.listCatalogTables.mockImplementation(async () => freshTables());
  api.indexStatus.mockResolvedValue(IDLE);
  api.updateTable.mockResolvedValue({});
  api.updateColumn.mockResolvedValue({});
});

afterEach(() => {
  vi.useRealTimers();
});

describe("connection selection and loading", () => {
  it("selects the first connection and loads its tables", async () => {
    await renderLoaded();
    expect(screen.getByLabelText("Connection")).toHaveValue(postgresConnection.id);
    expect(api.listCatalogTables).toHaveBeenCalledWith(postgresConnection.id);
  });

  it("honours ?connection= and reloads when the connection changes", async () => {
    nav.search = `connection=${duckdbConnection.id}`;
    renderPage();
    await waitFor(() =>
      expect(api.listCatalogTables).toHaveBeenCalledWith(duckdbConnection.id),
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Connection"),
      postgresConnection.id,
    );
    await waitFor(() =>
      expect(api.listCatalogTables).toHaveBeenLastCalledWith(postgresConnection.id),
    );
  });

  it("guides the user when there are no connections", async () => {
    api.listConnections.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByRole("link", { name: "Add a connection" })).toBeVisible();
  });

  it("shows an error when connections fail to load", async () => {
    api.listConnections.mockRejectedValue(
      new api.ApiError("x", "Could not reach the server."),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the server.",
    );
  });

  it("shows loading skeletons, then an import prompt for an empty catalog", async () => {
    api.listCatalogTables.mockResolvedValue([]);
    api.importCatalog.mockResolvedValue({});
    renderPage();
    expect(await screen.findByText("Import the schema to get started")).toBeVisible();
    api.listCatalogTables.mockImplementation(async () => freshTables());
    const emptyState = screen
      .getByText("Import the schema to get started")
      .closest("div") as HTMLElement;
    await userEvent.click(
      within(emptyState).getByRole("button", { name: "Import schema" }),
    );
    expect(
      await screen.findByRole("checkbox", { name: "Include public.orders" }),
    ).toBeVisible();
  });
});

describe("table list and stats", () => {
  it("shows selection stats and description dots", async () => {
    await renderLoaded();
    expect(screen.getByText("1 of 2 tables selected · 1 described")).toBeVisible();
    const list = screen.getByRole("list", { name: "Tables" });
    expect(within(list).getAllByRole("img", { name: "Has description" })).toHaveLength(1);
    expect(within(list).getByText("~50.0k rows")).toBeVisible();
  });

  it("filters tables by search", async () => {
    await renderLoaded();
    await userEvent.type(screen.getByLabelText("Search tables"), "event");
    expect(screen.queryByRole("checkbox", { name: "Include public.orders" })).toBeNull();
    expect(
      screen.getByRole("checkbox", { name: "Include analytics.events" }),
    ).toBeVisible();
    await userEvent.clear(screen.getByLabelText("Search tables"));
    await userEvent.type(screen.getByLabelText("Search tables"), "zzz");
    expect(screen.getByText(/No tables match/)).toBeVisible();
  });

  it("toggles inclusion optimistically and persists it", async () => {
    await renderLoaded();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Include analytics.events" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "Include analytics.events" }),
    ).toBeChecked();
    expect(api.updateTable).toHaveBeenCalledWith("t-events", { selected: true });
    expect(await screen.findByText("2 of 2 tables selected · 1 described")).toBeVisible();
  });

  it("reverts and alerts when saving fails", async () => {
    api.updateTable.mockRejectedValue(new api.ApiError("x", "Could not save"));
    await renderLoaded();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Include analytics.events" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save");
    expect(
      screen.getByRole("checkbox", { name: "Include analytics.events" }),
    ).not.toBeChecked();
  });

  it("selects all and none, warning when nothing is selected", async () => {
    await renderLoaded();
    await userEvent.click(screen.getByRole("button", { name: "Select all" }));
    await waitFor(() =>
      expect(api.updateTable).toHaveBeenCalledWith("t-events", { selected: true }),
    );
    expect(api.updateTable).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Select none" }));
    expect(await screen.findByText(/No tables selected/)).toBeVisible();
  });
});

describe("table detail and autosave", () => {
  it("shows the first table's columns and switches tables", async () => {
    await renderLoaded();
    const detail = screen.getByRole("region", { name: "Details of public.orders" });
    expect(
      within(detail).getByRole("table", { name: "Columns of public.orders" }),
    ).toBeVisible();
    expect(within(detail).getByText("integer")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Open analytics.events" }));
    expect(
      screen.getByRole("region", { name: "Details of analytics.events" }),
    ).toBeVisible();
  });

  it("autosaves a description on blur and shows Saved", async () => {
    await renderLoaded();
    const field = screen.getByLabelText("Description for public.orders");
    await userEvent.clear(field);
    await userEvent.type(field, "All orders");
    await userEvent.tab();
    await waitFor(() =>
      expect(api.updateTable).toHaveBeenCalledWith("t-orders", {
        description: "All orders",
      }),
    );
    expect(await screen.findByText("Saved")).toBeVisible();
  });

  it("does not save an unchanged description", async () => {
    await renderLoaded();
    await userEvent.click(screen.getByLabelText("Description for public.orders"));
    await userEvent.tab();
    expect(api.updateTable).not.toHaveBeenCalled();
  });

  it("reverts a column description that fails to save", async () => {
    api.updateColumn.mockRejectedValue(new api.ApiError("x", "Description too long"));
    await renderLoaded();
    const field = screen.getByLabelText("Description for public.orders.status");
    await userEvent.type(field, "new text");
    await userEvent.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Description too long");
    expect(field).toHaveValue("");
  });

  it("toggles a column and persists it", async () => {
    await renderLoaded();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Include public.orders.status" }),
    );
    expect(api.updateColumn).toHaveBeenCalledWith("c-orders-status", { selected: true });
  });
});

describe("import and index", () => {
  it("imports the schema, reloads, and marks a built index stale", async () => {
    api.importCatalog.mockResolvedValue({});
    api.indexStatus.mockResolvedValue({
      status: "done",
      indexed_tables: 1,
      indexed_columns: 3,
    });
    await renderLoaded();
    expect(await screen.findByText("Up to date")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Import schema" }));
    await waitFor(() =>
      expect(api.importCatalog).toHaveBeenCalledWith(postgresConnection.id),
    );
    expect(await screen.findByText("Stale")).toBeVisible();
    expect(api.listCatalogTables).toHaveBeenCalledTimes(2);
  });

  it("shows import errors", async () => {
    api.importCatalog.mockRejectedValue(new api.ApiError("x", "Import failed"));
    await renderLoaded();
    await userEvent.click(screen.getByRole("button", { name: "Import schema" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Import failed");
  });

  it("builds the index and polls until it is up to date", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.buildIndex.mockResolvedValue({
      status: "running",
      indexed_tables: 0,
      indexed_columns: 0,
    });
    await renderLoaded();
    expect(screen.getByText("Not built")).toBeVisible();

    api.indexStatus.mockResolvedValue({
      status: "done",
      indexed_tables: 2,
      indexed_columns: 5,
    });
    await userEvent.click(screen.getByRole("button", { name: "Build index" }));
    expect(await screen.findByText("Building…")).toBeVisible();
    expect(screen.getByRole("button", { name: "Build index" })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_STEP_MS + 50);
    });
    expect(await screen.findByText("Up to date")).toBeVisible();
    expect(screen.getByRole("button", { name: "Build index" })).toBeEnabled();
  });

  it("shows a failed build and index errors", async () => {
    api.indexStatus.mockResolvedValue({
      status: "failed",
      indexed_tables: 0,
      indexed_columns: 0,
    });
    await renderLoaded();
    expect(await screen.findByText("Build failed")).toBeVisible();
    api.buildIndex.mockRejectedValue(new api.ApiError("x", "Index service down"));
    await userEvent.click(screen.getByRole("button", { name: "Build index" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Index service down");
  });
});
