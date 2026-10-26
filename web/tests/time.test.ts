import { formatDuration, relativeTime } from "@/lib/time";

const NOW = new Date("2026-10-26T12:00:00Z");

describe("relativeTime", () => {
  it.each([
    ["2026-10-26T11:59:50Z", "just now"],
    ["2026-10-26T11:55:00Z", "5m ago"],
    ["2026-10-26T09:00:00Z", "3h ago"],
    ["2026-10-23T12:00:00Z", "3d ago"],
  ])("%s -> %s", (iso, expected) => {
    expect(relativeTime(iso, NOW)).toBe(expected);
  });

  it("falls back to a date for old timestamps and blank for garbage", () => {
    expect(relativeTime("2026-08-01T12:00:00Z", NOW)).toMatch(/Aug/);
    expect(relativeTime("nope", NOW)).toBe("");
  });
});

describe("formatDuration", () => {
  it("formats seconds and minutes", () => {
    expect(formatDuration(42_000)).toBe("42s");
    expect(formatDuration(125_000)).toBe("2m 5s");
    expect(formatDuration(-5)).toBe("0s");
  });
});
