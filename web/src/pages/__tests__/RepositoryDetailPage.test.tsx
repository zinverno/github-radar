import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as endpoints from "../../lib/endpoints";
import type { FieldDelta, RepositoryDetail } from "../../lib/types";
import { RepositoryDetailPage } from "../RepositoryDetailPage";

function detail(days: number | null, change: number): RepositoryDetail {
  const end = "2026-10-16T12:00:00Z";
  const start = new Date(Date.parse(end) - (days ?? 0) * 86400000).toISOString();
  const delta: FieldDelta | null = days === null ? null : {
    field: "stars", window_days: 7, base_value: 100, current_value: 100 + change,
    delta: change, per_day: change / days, growth_pct: change, base_gap_hours: 0,
    window: { span_days: days, complete: days === 7, start, end }, available: true,
  };
  return {
    id: 1, github_id: 1, full_name: "octo/one", owner_login: "octo", owner_id: null,
    name: "one", description: null, html_url: "https://github.com/octo/one", homepage: null,
    default_branch: "main", primary_language: null, visibility: "public",
    flags: { fork: false, archived: false, disabled: false, template: false },
    created_at: null, updated_at: null, pushed_at: null, first_seen_at: start, last_seen_at: end,
    stale_days: 0, topics: [], snapshot_count: days === null ? 1 : 2,
    metrics: {
      latest_captured_at: end, latest_pushed_at: null, snapshot_count: days === null ? 1 : 2,
      latest_counts: { stars: 100 + change, forks: 10 }, history_days: days,
      stars_7d: delta, stars_30d: delta && {
        ...delta, window_days: 30, window: { ...delta.window, complete: false },
      },
    },
    momentum: null, confidence: null,
    trend: {
      trend: days === null ? "new" : "steady", label: "Steady",
      explanation: days === null ? "Not enough history" : `Observed over ${days} days`,
      growth_pct: delta?.growth_pct ?? null, recency: 1, window_days: 7, window: delta?.window ?? null,
    },
    stars_7d: delta?.delta ?? null, stars_30d: delta?.delta ?? null,
    contributors: [], graph: null, bridge: null,
  };
}

describe("RepositoryDetailPage observation coverage", () => {
  it.each<[number | null, number]>([[null, 0], [2, 0], [2, 10], [7, 0]])(
    "distinguishes %s observed days and %s growth",
    async (days, change) => {
      vi.spyOn(endpoints, "repositoryDetail").mockResolvedValue(detail(days, change));
      render(
        <MemoryRouter initialEntries={["/repositories/octo/one"]}>
          <Routes><Route path="/repositories/:owner/:name" element={<RepositoryDetailPage />} /></Routes>
        </MemoryRouter>,
      );
      const coverage = await screen.findByText("Star coverage (7d requested)");
      expect(coverage.parentElement).toHaveTextContent(days === null
        ? "unavailable (7d requested)"
        : `${days}d observed; ${days === 7 ? "complete" : "partial"} coverage (7d requested)`);
      expect(screen.getByText("Star delta (7d requested)").parentElement)
        .toHaveTextContent(days === null ? "—" : String(change));
      expect(screen.getByText("Observed star growth").parentElement)
        .toHaveTextContent(days === null ? "—" : `${change === 0 ? "0.0" : "+10.0"}%`);
      if (days === 2) expect(screen.getByText("Steady · partial")).toBeInTheDocument();
      expect(screen.queryByText("Stars growth (7d)")).not.toBeInTheDocument();
    },
  );
});
