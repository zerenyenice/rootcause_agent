import { parseWhyChain } from "@/lib/whychain";

const valid = {
  problem: { what: "Scrap up", when: "Jan", where: "L02", impact: "320 units" },
  levels: [
    {
      question: "Why?",
      answer: "Bad lot",
      evidence: [{ sql: "SELECT 1", rows_summary: "1 row" }],
      confidence: 0.9,
    },
  ],
  root_cause: { statement: "Sampling too small", type: "process", confidence: 0.7 },
  validation: [{ link: "lot -> scrap", check_sql: "SELECT 1", passed: true }],
  countermeasures: [{ type: "preventive", action: "Sample more", owner: "QA" }],
  branches: [],
  partial: false,
  notes: "",
};

describe("parseWhyChain", () => {
  it("parses a complete chain from a JSON string", () => {
    const chain = parseWhyChain(JSON.stringify(valid));
    expect(chain?.problem.what).toBe("Scrap up");
    expect(chain?.levels[0]?.evidence[0]).toEqual({
      sql: "SELECT 1",
      rowsSummary: "1 row",
    });
    expect(chain?.rootCause).toEqual({
      statement: "Sampling too small",
      type: "process",
      confidence: 0.7,
    });
    expect(chain?.countermeasures[0]).toEqual({
      type: "preventive",
      action: "Sample more",
      owner: "QA",
    });
    expect(chain?.validation[0]?.passed).toBe(true);
  });

  it("accepts a fenced JSON block", () => {
    expect(parseWhyChain("```json\n" + JSON.stringify(valid) + "\n```")).not.toBeNull();
  });

  it("accepts an already parsed object", () => {
    expect(parseWhyChain(valid)?.levels).toHaveLength(1);
  });

  it.each([
    ["plain prose", "The root cause is X."],
    ["broken JSON", '{"problem": '],
    ["JSON without a problem", '{"levels": []}'],
    ["an array", "[1,2]"],
    ["null", null],
  ])("returns null for %s", (_name, input) => {
    expect(parseWhyChain(input)).toBeNull();
  });

  it("clamps confidence, drops malformed items and defaults unknown types", () => {
    const chain = parseWhyChain({
      problem: { what: "w" },
      levels: [
        { question: "q", answer: "a", evidence: [{ nope: 1 }, "x"], confidence: 7 },
        "junk",
        { question: "", answer: "" },
      ],
      root_cause: { statement: "s", type: "alien", confidence: "high" },
      countermeasures: [{ type: "mystery", action: "do it" }, { action: "" }],
      validation: [{ link: "" }],
    });
    expect(chain?.problem).toEqual({ what: "w", when: "", where: "", impact: "" });
    expect(chain?.levels).toHaveLength(1);
    expect(chain?.levels[0]?.confidence).toBe(1);
    expect(chain?.levels[0]?.evidence).toEqual([]);
    expect(chain?.rootCause).toEqual({ statement: "s", type: null, confidence: null });
    expect(chain?.countermeasures).toEqual([
      { type: "immediate", action: "do it", owner: null },
    ]);
    expect(chain?.validation).toEqual([]);
  });

  it("parses nested branches but stops at a safe depth", () => {
    let nested: Record<string, unknown> = { ...valid, branches: [] };
    for (let i = 0; i < 6; i++) nested = { ...valid, branches: [nested] };
    const chain = parseWhyChain(nested);
    let depth = 0;
    for (let c = chain; c && c.branches[0]; c = c.branches[0]) depth += 1;
    expect(depth).toBe(3);
  });

  it("keeps the partial flag and notes", () => {
    const chain = parseWhyChain({
      ...valid,
      partial: true,
      notes: "budget",
      root_cause: null,
    });
    expect(chain?.partial).toBe(true);
    expect(chain?.notes).toBe("budget");
    expect(chain?.rootCause).toBeNull();
  });
});
