import { describe, expect, it } from "vitest";

import {
  parseRepoFilters,
  repoFiltersToSearchParams,
} from "../repoFilters";

describe("repoFilters", () => {
  it("parses defaults from an empty query string", () => {
    const filters = parseRepoFilters(new URLSearchParams());
    expect(filters).toEqual({
      offset: 0,
      sort: "last_seen",
      order: "desc",
      language: "",
      trend: "",
      minStars: "",
      maxStars: "",
      hideArchived: false,
      hideForks: false,
    });
  });

  it("round-trips non-default values through the query string", () => {
    const filters = parseRepoFilters(
      new URLSearchParams(
        "sort=stars&order=asc&language=go&trend=rising&minStars=10&maxStars=500&archived=false&fork=false&offset=25",
      ),
    );
    expect(filters).toEqual({
      offset: 25,
      sort: "stars",
      order: "asc",
      language: "go",
      trend: "rising",
      minStars: "10",
      maxStars: "500",
      hideArchived: true,
      hideForks: true,
    });
    const back = repoFiltersToSearchParams(filters).toString();
    expect(back).toContain("sort=stars");
    expect(back).toContain("order=asc");
    expect(back).toContain("archived=false");
    expect(back).toContain("fork=false");
    expect(back).toContain("offset=25");
  });

  it("falls back to sensible defaults for unknown values", () => {
    const filters = parseRepoFilters(new URLSearchParams("sort=size&trend=boom&order=sideways"));
    expect(filters.sort).toBe("last_seen");
    expect(filters.trend).toBe("");
    expect(filters.order).toBe("desc");
  });

  it("drops default values from the query string", () => {
    const sp = repoFiltersToSearchParams({
      offset: 0,
      sort: "last_seen",
      order: "desc",
      language: "",
      trend: "",
      minStars: "",
      maxStars: "",
      hideArchived: false,
      hideForks: false,
    });
    expect(sp.toString()).toBe("");
  });
});