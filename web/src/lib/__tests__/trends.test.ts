import { describe, expect, it } from "vitest";

import { observationWindowLabel, trendDisplay, TREND_CLASS_NEW } from "../trends";

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

describe("observationWindowLabel", () => {
  it.each([[2, false], [0.25, false], [7, true], [8, true], [12, false]])(
    "reports %s actual days, separately from the target",
    (span, complete) => {
      expect(observationWindowLabel({ span_days: span, complete, start: null, end: null }, 7))
        .toBe(`${span}d observed; ${complete ? "complete" : "partial"} coverage (7d requested)`);
    },
  );

  it("keeps missing coverage unavailable", () => {
    expect(observationWindowLabel(null, 7)).toBe("unavailable (7d requested)");
  });
});
