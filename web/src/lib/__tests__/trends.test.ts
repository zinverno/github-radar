import { describe, expect, it } from "vitest";

import { trendDisplay, TREND_CLASS_NEW } from "../trends";

describe("trendDisplay", () => {
  it("maps 'new' to an accurate explanatory label", () => {
    expect(trendDisplay("new")).toBe("Insufficient history");
    expect(trendDisplay(TREND_CLASS_NEW)).toBe("Insufficient history");
  });

  it("maps other classes as-is (defensive fallback)", () => {
    expect(trendDisplay("rising")).toBe("Rising");
    expect(trendDisplay("steady")).toBe("Steady");
    expect(trendDisplay("declining")).toBe("Declining");
    expect(trendDisplay("inactive")).toBe("Inactive");
  });
});