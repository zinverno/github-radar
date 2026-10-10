import { describe, expect, it } from "vitest";

import { compact, dateTime, pct, relative, score, signed } from "../format";

describe("format", () => {
  it("compacts large numbers", () => {
    expect(compact(1500)).toBe("1.5K");
    expect(compact(null)).toBe("—");
  });

  it("formats percentages with a sign", () => {
    expect(pct(0.25)).toBe("+25.0%");
    expect(pct(-0.1)).toBe("-10.0%");
    expect(pct(null)).toBe("—");
  });

  it("formats signed integer deltas", () => {
    expect(signed(3)).toBe("+3");
    expect(signed(-2)).toBe("-2");
    expect(signed(null)).toBe("—");
  });

  it("keeps two decimals for scores", () => {
    expect(score(0.25)).toBe("0.25");
    expect(score(null)).toBe("—");
  });

  it("renders ISO timestamps as dates", () => {
    expect(dateTime("2026-10-10T12:00:00Z")).toContain("2026");
    expect(dateTime(null)).toBe("—");
    expect(dateTime("not-a-date")).toBe("—");
  });

  it("expresses relative times", () => {
    const now = Date.now();
    expect(relative(new Date(now - 60_000).toISOString())).toBe("1m ago");
    expect(relative(null)).toBe("—");
  });
});