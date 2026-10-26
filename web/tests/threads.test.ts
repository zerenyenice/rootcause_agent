import { deleteThread, loadThreadMessages, searchThreads } from "@/lib/threads";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

describe("searchThreads", () => {
  it("maps thread summaries, newest first, with a fallback title", async () => {
    fetchMock.mockResolvedValueOnce(
      json([
        {
          thread_id: "t1",
          created_at: "2026-10-01T10:00:00Z",
          updated_at: "2026-10-02T10:00:00Z",
          metadata: { title: "  Scrap rate ", connection_id: "c1" },
        },
        { thread_id: "t2", created_at: "2026-09-01T10:00:00Z", metadata: {} },
        { nope: true },
      ]),
    );
    const threads = await searchThreads();
    expect(threads).toEqual([
      {
        id: "t1",
        title: "Scrap rate",
        connectionId: "c1",
        createdAt: "2026-10-01T10:00:00Z",
        updatedAt: "2026-10-02T10:00:00Z",
      },
      {
        id: "t2",
        title: "New investigation",
        connectionId: null,
        createdAt: "2026-09-01T10:00:00Z",
        updatedAt: "2026-09-01T10:00:00Z",
      },
    ]);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/threads\/search$/);
    expect(JSON.parse(init.body as string)).toEqual({ limit: 50, offset: 0 });
  });

  it.each([
    ["a 404 (no history endpoint)", () => json({ error: "nf" }, 404)],
    ["a non-array body", () => json({ threads: [] })],
  ])("returns null for %s", async (_name, make) => {
    fetchMock.mockResolvedValueOnce(make());
    expect(await searchThreads()).toBeNull();
  });

  it("returns null when the service is unreachable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("down"));
    expect(await searchThreads()).toBeNull();
  });
});

describe("deleteThread", () => {
  it("is true on success and encodes the id", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await deleteThread("a/b")).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/threads\/a%2Fb$/);
    expect(init.method).toBe("DELETE");
  });

  it("is false on failure or network error", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));
    expect(await deleteThread("t")).toBe(false);
    fetchMock.mockRejectedValueOnce(new TypeError("down"));
    expect(await deleteThread("t")).toBe(false);
  });
});

describe("loadThreadMessages", () => {
  it("returns the messages of a thread, skipping malformed entries", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ values: { messages: [{ type: "human", content: "q" }, 5, {}] } }),
    );
    expect(await loadThreadMessages("t")).toEqual([{ type: "human", content: "q" }]);
  });

  it("returns an empty list when the state has no messages", async () => {
    fetchMock.mockResolvedValueOnce(json({ values: {} }));
    expect(await loadThreadMessages("t")).toEqual([]);
  });

  it.each([
    ["not found", () => json({}, 404)],
    ["no values", () => json({ next: [] })],
  ])("returns null when %s", async (_name, make) => {
    fetchMock.mockResolvedValueOnce(make());
    expect(await loadThreadMessages("t")).toBeNull();
  });

  it("returns null on network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("down"));
    expect(await loadThreadMessages("t")).toBeNull();
  });
});
