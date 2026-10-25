import { buildResume, parseApprovalRequest } from "@/lib/interrupt";

describe("interrupt helpers", () => {
  it("builds approve and reject resume payloads", () => {
    expect(buildResume("approve")).toEqual({ decisions: [{ type: "approve" }] });
    expect(buildResume("reject")).toEqual({ decisions: [{ type: "reject" }] });
  });

  it("parses an approval request with an estimate", () => {
    const req = parseApprovalRequest({
      action_requests: [
        { name: "run_sql", args: { sql: "SELECT 1", secret: "s" }, description: "d" },
      ],
    });
    expect(req).toEqual({ toolName: "run_sql", description: "d", sql: "SELECT 1" });
  });

  it("falls back to defaults and ignores a non-string sql", () => {
    const req = parseApprovalRequest({
      action_requests: [{ args: { sql: { a: 1 } } }],
    });
    expect(req).toEqual({ toolName: "tool", description: "", sql: null });
  });

  it.each([null, "x", {}, { action_requests: [] }, { action_requests: ["x"] }])(
    "returns null for malformed value %j",
    (value) => {
      expect(parseApprovalRequest(value)).toBeNull();
    },
  );
});
