import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HashRouter } from "react-router-dom";
import { App } from "../App";
import { api } from "../lib/api";
import { graphEdges, graphNodes, listRepositories, repositoryDetail, topicDetail } from "../lib/endpoints";

vi.mock("../lib/preview", () => ({ STATIC_PREVIEW: true }));
const fetcher = vi.fn(() => Promise.reject(new Error("Preview must never fetch an API")));
beforeEach(() => { vi.stubGlobal("fetch", fetcher); fetcher.mockClear(); });
afterEach(() => { expect(fetcher).not.toHaveBeenCalled(); vi.unstubAllGlobals(); });

function open(path: string) {
  window.history.replaceState(null, "", `/github-radar/#${path}`);
  return render(<HashRouter><App /></HashRouter>);
}

it("filters and sorts the fictional catalog, preserving empty results and pagination", async () => {
  expect((await listRepositories({ sort: "stars" })).items[0]?.full_name).toBe("demo-lab/trace-board");
  expect((await listRepositories({ language: "typescript", fork: false })).items.map(r => r.full_name))
    .toEqual(["demo-lab/signal-kit"]);
  expect((await listRepositories({ minStars: 400, maxStars: 1100, archived: false })).total).toBe(1);
  expect((await listRepositories({ trend: "new" })).items[0]?.full_name).toBe("demo-lab/new-spark");
  expect((await listRepositories({ language: "not-in-the-demo" })).total).toBe(0);
  expect(await listRepositories({ limit: 2, offset: 2 })).toMatchObject({ total: 6, offset: 2, has_more: true });
});

it("keeps measured zero, unavailable history, and synthetic coverage distinct", async () => {
  const growing = await repositoryDetail("demo-lab/signal-kit");
  expect(growing.stars_7d).toBe(100);
  expect(growing.metrics.stars_7d?.growth_pct).toBeCloseTo(100 / 900 * 100);
  expect(growing.metrics.stars_7d?.window).toMatchObject({ complete: true, span_days: 7 });
  expect((await repositoryDetail("demo-lab/trace-board")).stars_7d).toBe(0);
  expect((await repositoryDetail("demo-lab/new-spark")).metrics.stars_7d).toBeNull();
  expect((await topicDetail("demo-empty")).repositories).toEqual([]);
  const nodes = await graphNodes(null);
  for (const edge of await graphEdges()) for (const end of [edge.source, edge.target]) {
    expect(nodes.some(n => n.node_type === end.node_type && n.node_id === end.node_id)).toBe(true);
  }
});

it("rejects writes and unavailable endpoints without any network fallback", async () => {
  await expect(api("/ai/generate", { method: "POST" })).rejects.toMatchObject({ status: 405 });
  for (const path of ["/workspace/developers", "/ai/status", "/unknown", "https://private.invalid", "/repositories/missing"]) {
    await expect(api(path)).rejects.toMatchObject({ status: 404 });
  }
});

it("labels demo metrics, uses hash links and preserves deep links on remount", async () => {
  const view = open("/repositories/demo-lab/signal-kit");
  await screen.findByRole("heading", { name: "demo-lab/signal-kit" });
  expect(screen.getByRole("complementary", { name: "Demo data disclosure" })).toHaveTextContent("DEMO DATA");
  expect(screen.queryByRole("link", { name: /demo-lab\/signal-kit/ })).not.toBeInTheDocument();
  const topic = screen.getByRole("link", { name: "developer-tools" });
  expect(topic).toHaveAttribute("href", "#/topics/developer-tools");
  fireEvent.click(topic);
  await screen.findByRole("heading", { name: "Topic: developer-tools" });
  view.unmount();
  render(<HashRouter><App /></HashRouter>);
  await screen.findByRole("heading", { name: "Topic: developer-tools" });
  fireEvent.click(screen.getByRole("link", { name: "Skip to content" }));
  expect(document.activeElement).toBe(screen.getByRole("main"));
  expect(window.location.hash).toBe("#/topics/developer-tools");
});

it.each(["/ai", "/workspace", "/workspace/repositories"])("blocks live tools at %s", async path => {
  open(path);
  await screen.findByRole("heading", { name: "Unavailable in preview" });
  expect(screen.queryByRole("link", { name: "AI" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
});

it("keeps empty filters usable without suggesting a backend command", async () => {
  open("/repositories?language=unknown");
  await screen.findByText("No repositories match the current filters.");
  expect(screen.queryByText("github-radar discover")).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Language" })).toHaveValue("unknown");
});

it("preserves the input and keyboard focus while changing filters", async () => {
  open("/repositories");
  const input = await screen.findByRole("searchbox", { name: "Language" });
  await userEvent.type(input, "TypeScript");
  expect(input).toHaveValue("TypeScript");
  expect(input).toHaveFocus();
});
