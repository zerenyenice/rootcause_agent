import { buildResume, parseApprovalRequests } from "@/lib/interrupt";

describe("interrupt helpers", () => {
  it("builds approve and reject resume payloads", () => {
    expect(buildResume("approve")).toEqual({ decisions: [{ type: "approve" }] });
    expect(buildResume("reject")).toEqual({ decisions: [{ type: "reject" }] });
  });

  it("parses an approval request with an estimate", () => {
    const req = parseApprovalRequests({
      action_requests: [
        { name: "run_sql", args: { sql: "SELECT 1", secret: "s" }, description: "d" },
      ],
    });
    expect(req).toEqual([{ toolName: "run_sql", description: "d", sql: "SELECT 1" }]);
  });

  it("falls back to defaults and ignores a non-string sql", () => {
    const req = parseApprovalRequests({
      action_requests: [{ args: { sql: { a: 1 } } }],
    });
    expect(req).toEqual([{ toolName: "tool", description: "", sql: null }]);
  });

  it.each([null, "x", {}, { action_requests: [] }])(
    "returns null for malformed value %j",
    (value) => {
      expect(parseApprovalRequests(value)).toBeNull();
    },
  );

  it("keeps a slot for every request, including malformed ones", () => {
    const req = parseApprovalRequests({
      action_requests: [
        { name: "run_sql", args: { sql: "A" } },
        "x",
        { args: { sql: "C" } },
      ],
    });
    expect(req?.map((r) => r.sql)).toEqual(["A", null, "C"]);
  });

  it("builds one decision per request", () => {
    expect(buildResume("reject", 3).decisions).toHaveLength(3);
  });
});
