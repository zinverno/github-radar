import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceDeveloper, WorkspaceRepository } from "../../lib/workspace";
import { WorkspaceLayout } from "../WorkspaceLayout";
import { WorkspacePage } from "../WorkspacePage";

const token = "a".repeat(40);
const state = { saved: true, notes: "Private <script>bad()</script>", relationship_status: "interested" as const, version: "b".repeat(32), updated_at: "2026-10-17T10:00:00Z" };
const developer: WorkspaceDeveloper = {
  id: 1, login: "octo", name: "Octo", followers: 10, first_seen_at: "2026-10-17T10:00:00Z",
  associated_repositories: [], languages: ["Python"], relevance: 0.6, activity: null,
  ecosystem: null, best_topic: null, topics: ["mcp"], public_email: null,
  emerging: { label: "INSUFFICIENT_DATA", score: 0, confidence_score: 0, confidence_level: "LOW", explanation: "Only one observation", components: {} },
  contacts: [{ label: "GitHub profile", url: "https://github.com/octo" }], workspace: state,
};
const repository: WorkspaceRepository = {
  id: 2, full_name: "octo/repo", description: "Sample repository", primary_language: "Python", topics: ["mcp"],
  contacts: [{ label: "GitHub repository", url: "https://github.com/octo/repo" }], workspace: state,
};

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function failure(status: number) {
  return response({ error: { code: "test_error", message: status === 409 ? "Entry changed; reload." : "Access denied.", details: {} } }, status);
}
function setup(kind: "developers" | "repositories" = "developers", total = 1) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/session")) return response({ access: "local-admin" });
    if (init?.method === "PUT") return response({ ...state, ...JSON.parse(init.body as string) });
    if (init?.method === "DELETE") return response({ deleted: true });
    const offset = Number(new URL(url, "http://localhost").searchParams.get("offset") ?? 0);
    return response({ items: [kind === "developers" ? developer : repository], limit: 25, offset, total, has_more: offset + 25 < total });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<MemoryRouter initialEntries={[`/workspace/${kind}`]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <Routes><Route path="workspace" element={<WorkspaceLayout />}>
      <Route path="developers" element={<WorkspacePage kind="developers" />} />
      <Route path="repositories" element={<WorkspacePage kind="repositories" />} />
    </Route></Routes>
  </MemoryRouter>);
  return fetcher;
}
async function unlock() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Admin token"), token);
  await user.click(screen.getByRole("button", { name: "Unlock" }));
  await screen.findByRole("button", { name: "Edit private details" });
  return user;
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Private networking workspace", () => {
  it("does not request private data before authentication; never persists the token", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const fetcher = setup();
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
    const user = await unlock();
    expect(screen.getByText(state.notes)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("Only one observation")).toBeInTheDocument();
    expect(screen.getByText("0.60")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /GitHub profile/ })).toHaveAttribute("rel", "noopener noreferrer");
    expect(storage).not.toHaveBeenCalled();
    for (const [url, init] of fetcher.mock.calls) {
      expect(url).not.toContain(token);
      expect(init).toMatchObject({ cache: "no-store", credentials: "omit", redirect: "error", headers: { Authorization: `Bearer ${token}` } });
    }
    await user.click(screen.getByRole("button", { name: "Lock workspace" }));
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Admin token")).toHaveValue("");
  });

  it("keeps private content hidden after failed authentication", async () => {
    const fetcher = setup();
    fetcher.mockResolvedValue(failure(401));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Admin token"), token);
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
  });

  it("saves developer status and notes, preserving the expected version", async () => {
    const fetcher = setup();
    const user = await unlock();
    await user.click(screen.getByRole("button", { name: "Edit private details" }));
    await user.click(screen.getByLabelText("Saved to watchlist"));
    await user.selectOptions(screen.getByLabelText("Relationship status"), "connected");
    await user.clear(screen.getByLabelText("Personal notes"));
    await user.type(screen.getByLabelText("Personal notes"), "Met at a meetup");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Private entry updated.");
    const mutation = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(mutation?.[0]).toBe("/api/v1/workspace/developers/1");
    expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ saved: false, notes: "Met at a meetup", relationship_status: "connected", version: state.version });
  });

  it("retains a draft when the server rejects a conflicting edit", async () => {
    const fetcher = setup();
    const user = await unlock();
    await user.click(screen.getByRole("button", { name: "Edit private details" }));
    await user.type(screen.getByLabelText("Personal notes"), " draft");
    fetcher.mockResolvedValueOnce(failure(409));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Entry changed");
    expect(screen.getByLabelText("Personal notes")).toHaveValue(state.notes + " draft");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("filters saved developers by topic, relationship and deterministic relevance", async () => {
    const fetcher = setup();
    const user = await unlock();
    await user.selectOptions(screen.getByLabelText("Saved status"), "all");
    await user.type(screen.getByLabelText("Topic"), "mcp");
    await user.selectOptions(screen.getByLabelText("Relationship"), "connected");
    await user.type(screen.getByLabelText("Minimum relevance"), "0.5");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(fetcher.mock.calls.at(-1)?.[0]).toContain("min_relevance=0.5"));
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("saved=all");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("topic=mcp");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("status=connected");
  });

  it("paginates repositories and requires confirmation before deletion", async () => {
    const fetcher = setup("repositories", 26);
    const user = await unlock();
    expect(screen.queryByLabelText("Minimum relevance")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(fetcher.mock.calls.at(-1)?.[0]).toContain("offset=25"));
    await screen.findByRole("button", { name: "Edit private details" });
    await user.click(screen.getByRole("button", { name: "Edit private details" }));
    await user.click(screen.getByRole("button", { name: "Delete private entry…" }));
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirm deletion" }));
    await screen.findByText("Private entry updated.");
    expect(fetcher.mock.calls.find(([, init]) => init?.method === "DELETE")?.[0])
      .toBe(`/api/v1/workspace/repositories/2?version=${state.version}`);
  });

  it("locks and removes cached data when authorization is lost", async () => {
    const fetcher = setup();
    const user = await unlock();
    fetcher.mockResolvedValueOnce(failure(401));
    await user.selectOptions(screen.getByLabelText("Saved status"), "all");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await screen.findByLabelText("Admin token");
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
  });

  it("locks on pagehide so history restoration does not display private notes", async () => {
    setup(); await unlock();
    act(() => { fireEvent(window, new Event("pagehide")); });
    expect(screen.getByLabelText("Admin token")).toHaveValue("");
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
  });

  it("stays locked if an unlock response arrives after pagehide", async () => {
    const fetcher = setup();
    let finish!: (value: Response) => void;
    fetcher.mockReturnValueOnce(new Promise<Response>(resolve => { finish = resolve; }));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Admin token"), token);
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    act(() => { fireEvent(window, new Event("pagehide")); });
    await act(async () => { finish(response({ access: "local-admin" })); });
    expect(screen.getByLabelText("Admin token")).toHaveValue("");
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([403, 503])("explains unavailable access (%s) without showing private data", async status => {
    const fetcher = setup();
    const message = status === 503 ? "The private workspace is not configured." : "Workspace requires direct local admin access.";
    fetcher.mockResolvedValue(response({ error: { code: "unavailable", message, details: {} } }, status));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Admin token"), token);
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(state.notes)).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("guides an empty watchlist through adding the first saved entry", async () => {
    const fetcher = setup();
    const entry = { ...developer, workspace: { ...state, saved: false, notes: "", version: null } };
    fetcher.mockImplementation(async (url, init) => {
      if (url.endsWith("/session")) return response({ access: "local-admin" });
      if (init?.method === "PUT") return response({ ...state, notes: "" });
      const items = url.includes("saved=all") ? [entry] : [];
      return response({ items, total: items.length, offset: 0, limit: 25, has_more: false });
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Admin token"), token);
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByText(/No matching entries/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Saved status"), "all");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await user.click(await screen.findByRole("button", { name: "Add to workspace" }));
    await user.click(screen.getByLabelText("Saved to watchlist"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Private entry updated.");
    const mutation = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(mutation?.[1]?.body as string)).toMatchObject({ saved: true, notes: "", version: null });
  });
});
