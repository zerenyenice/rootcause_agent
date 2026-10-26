import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppShell } from "@/components/shell/AppShell";
import { THEME_STORAGE_KEY } from "@/lib/theme";

const nav = vi.hoisted(() => ({ pathname: "/", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push }),
}));

const threads = vi.hoisted(() => ({
  searchThreads: vi.fn(),
  deleteThread: vi.fn(),
}));
vi.mock("@/lib/threads", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/threads")>()),
  searchThreads: threads.searchThreads,
  deleteThread: threads.deleteThread,
}));

const NOW = Date.now();
const THREADS = [
  {
    id: "t1",
    title: "Why did scrap rise?",
    connectionId: "c1",
    createdAt: new Date(NOW - 3_600_000).toISOString(),
    updatedAt: new Date(NOW - 3_000_000).toISOString(),
  },
  {
    id: "t2",
    title: "Downtime on line 3",
    connectionId: null,
    createdAt: new Date(NOW - 172_800_000).toISOString(),
    updatedAt: new Date(NOW - 172_800_000).toISOString(),
  },
];

function renderShell() {
  return render(
    <AppShell>
      <p>page-content</p>
    </AppShell>,
  );
}

beforeEach(() => {
  nav.pathname = "/";
  nav.push.mockClear();
  threads.searchThreads.mockResolvedValue(structuredClone(THREADS));
  threads.deleteThread.mockResolvedValue(true);
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("AppShell navigation", () => {
  it("renders landmarks, the page content and main links", async () => {
    renderShell();
    await screen.findByText("Why did scrap rise?");
    expect(screen.getByRole("main")).toHaveTextContent("page-content");
    const nav = within(
      screen.getAllByRole("navigation", { name: "Main" })[0] as HTMLElement,
    );
    expect(nav.getByRole("link", { name: "Chat" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(nav.getByRole("link", { name: "Connections" })).toHaveAttribute(
      "href",
      "/connections",
    );
    expect(nav.getByRole("link", { name: "Catalog" })).toHaveAttribute(
      "href",
      "/catalog",
    );
  });

  it("marks only the current route", async () => {
    nav.pathname = "/catalog";
    renderShell();
    await screen.findByText("Downtime on line 3");
    expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Chat" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("opens the sidebar as a drawer on small screens and closes it on Escape", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getAllByRole("complementary", { name: "Sidebar" })).toHaveLength(2);
    await userEvent.keyboard("{Escape}");
    expect(screen.getAllByRole("complementary", { name: "Sidebar" })).toHaveLength(1);
  });
});

describe("thread history", () => {
  it("lists past investigations with relative times", async () => {
    renderShell();
    expect(await screen.findByText("Why did scrap rise?")).toBeInTheDocument();
    expect(screen.getByText("50m ago")).toBeInTheDocument();
    expect(screen.getByText("2d ago")).toBeInTheDocument();
  });

  it("hides history when the service has no history endpoint", async () => {
    threads.searchThreads.mockResolvedValue(null);
    renderShell();
    await waitFor(() => expect(threads.searchThreads).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Investigation history" })).toBeNull();
  });

  it("shows an empty hint when there are no threads", async () => {
    threads.searchThreads.mockResolvedValue([]);
    renderShell();
    expect(await screen.findByText("No investigations yet.")).toBeInTheDocument();
  });

  it("opens a thread, marks it active and navigates to chat from other pages", async () => {
    nav.pathname = "/catalog";
    renderShell();
    await userEvent.click(
      await screen.findByRole("button", { name: /^Why did scrap rise/ }),
    );
    expect(nav.push).toHaveBeenCalledWith("/");
    expect(screen.getByRole("button", { name: /^Why did scrap rise/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("starts a new investigation", async () => {
    renderShell();
    await userEvent.click(
      await screen.findByRole("button", { name: /^Why did scrap rise/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "New investigation" }));
    expect(
      screen.getByRole("button", { name: /^Why did scrap rise/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("confirms in-app before deleting a thread", async () => {
    const confirm = vi.spyOn(window, "confirm");
    renderShell();
    await screen.findByText("Downtime on line 3");

    await userEvent.click(
      screen.getByRole("button", { name: "Delete Downtime on line 3" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Delete investigation?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(threads.deleteThread).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Delete Downtime on line 3" }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() => expect(threads.deleteThread).toHaveBeenCalledWith("t2"));
    await waitFor(() => expect(screen.queryByText("Downtime on line 3")).toBeNull());
    expect(confirm).not.toHaveBeenCalled();
  });

  it("tells the user when deleting fails", async () => {
    threads.deleteThread.mockResolvedValue(false);
    renderShell();
    await screen.findByText("Downtime on line 3");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Downtime on line 3" }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
    expect(
      await screen.findByText("Could not delete the investigation."),
    ).toBeInTheDocument();
    expect(screen.getByText("Downtime on line 3")).toBeInTheDocument();
  });
});

describe("theme toggle", () => {
  it("applies and persists the chosen theme", async () => {
    renderShell();
    const group = screen.getAllByRole("radiogroup", { name: "Theme" })[0] as HTMLElement;
    await userEvent.click(within(group).getByLabelText("Dark"));
    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    await userEvent.click(within(group).getByLabelText("Light"));
    expect(document.documentElement).not.toHaveClass("dark");
  });
});
