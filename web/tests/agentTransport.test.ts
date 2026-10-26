import { AgentThreadTransport } from "@/lib/agentTransport";

const sdk = vi.hoisted(() => ({
  apiUrls: [] as string[],
  payloads: [] as unknown[],
}));

vi.mock("@langchain/langgraph-sdk/ui", () => ({
  FetchStreamTransport: class {
    constructor(private readonly options: { apiUrl: string }) {
      sdk.apiUrls.push(options.apiUrl);
    }
    async stream(payload: unknown) {
      sdk.payloads.push(payload);
      return (async function* () {})();
    }
  },
}));

const AGENT = "http://agent.test:8100";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function payload() {
  return { input: {}, signal: new AbortController().signal } as never;
}

describe("AgentThreadTransport", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    sdk.apiUrls.length = 0;
    sdk.payloads.length = 0;
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a thread on the first run and streams from its runs URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ thread_id: "t-1" }));
    const request = payload();
    void request;

    await new AgentThreadTransport(AGENT).stream(request);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${AGENT}/threads`);
    expect(init.method).toBe("POST");
    expect(sdk.apiUrls).toEqual([`${AGENT}/threads/t-1/runs/stream`]);
    expect(sdk.payloads[0]).toMatchObject({
      input: {},
      signal: expect.anything(),
      stream_subgraphs: true,
      streamSubgraphs: true,
    });
  });

  it("continues an existing thread after use() without creating one", async () => {
    const transport = new AgentThreadTransport(AGENT);
    transport.use("t-past");

    await transport.stream(payload());

    expect(fetchMock).not.toHaveBeenCalled();
    expect(transport.currentThreadId).toBe("t-past");
    expect(sdk.apiUrls).toEqual([`${AGENT}/threads/t-past/runs/stream`]);
  });

  it("reports a newly created thread to the listener", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ thread_id: "t-new" }));
    const onCreated = vi.fn();

    await new AgentThreadTransport(AGENT, onCreated).stream(payload());

    expect(onCreated).toHaveBeenCalledWith("t-new");
  });

  it("reuses the thread for later runs without another POST", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ thread_id: "t-1" }));
    const transport = new AgentThreadTransport(AGENT);

    await transport.stream(payload());
    await transport.stream(payload());
    await transport.stream(payload());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sdk.apiUrls).toEqual(Array(3).fill(`${AGENT}/threads/t-1/runs/stream`));
  });

  it("creates a new thread after reset()", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ thread_id: "t-1" }))
      .mockResolvedValueOnce(jsonResponse({ thread_id: "t-2" }));
    const transport = new AgentThreadTransport(AGENT);

    await transport.stream(payload());
    transport.reset();
    await transport.stream(payload());

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sdk.apiUrls).toEqual([
      `${AGENT}/threads/t-1/runs/stream`,
      `${AGENT}/threads/t-2/runs/stream`,
    ]);
  });

  it("url-encodes the thread id in the stream URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ thread_id: "a/b c" }));
    await new AgentThreadTransport(AGENT).stream(payload());
    expect(sdk.apiUrls).toEqual([`${AGENT}/threads/a%2Fb%20c/runs/stream`]);
  });

  it("rejects when thread creation returns a non-OK status, and retries next run", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "boom" }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ thread_id: "t-9" }));
    const transport = new AgentThreadTransport(AGENT);

    await expect(transport.stream(payload())).rejects.toThrow(/500/);
    expect(sdk.apiUrls).toEqual([]);

    await transport.stream(payload());
    expect(sdk.apiUrls).toEqual([`${AGENT}/threads/t-9/runs/stream`]);
  });

  it.each([
    ["missing thread_id", {}],
    ["non-string thread_id", { thread_id: 7 }],
    ["null body", null],
    ["a bare string", "t-1"],
  ])("rejects a malformed thread response (%s)", async (_name, body) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(body));
    await expect(new AgentThreadTransport(AGENT).stream(payload())).rejects.toThrow(
      /Unexpected thread response/,
    );
    expect(sdk.apiUrls).toEqual([]);
  });

  it("rejects when the response body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 200 }));
    await expect(new AgentThreadTransport(AGENT).stream(payload())).rejects.toThrow();
  });

  it("propagates a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    await expect(new AgentThreadTransport(AGENT).stream(payload())).rejects.toThrow(
      "network down",
    );
  });
});
