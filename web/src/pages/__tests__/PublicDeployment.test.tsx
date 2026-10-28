import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../../components/Layout";
import { LocalOnly } from "../../components/LocalOnly";
import { generateArtifact } from "../../lib/endpoints";
import { AiPage } from "../AiPage";

const token = "synthetic_admin_token_12345678901234567890";
afterEach(() => vi.unstubAllGlobals());

it.each(["public", "unknown", "failure"])("hides admin tools and blocks mounting in %s", async mode => {
  const fetcher = vi.fn().mockImplementation(() => mode === "failure"
    ? Promise.reject(new Error("offline"))
    : Promise.resolve(new Response(JSON.stringify({ mode }))));
  vi.stubGlobal("fetch", fetcher);
  render(<MemoryRouter initialEntries={["/ai"]}><Routes><Route element={<Layout />}>
    <Route path="/ai" element={<LocalOnly><AiPage /></LocalOnly>} />
  </Route></Routes></MemoryRouter>);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "AI" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate" })).not.toBeInTheDocument();
  expect(screen.getByText(/unavailable/)).toBeInTheDocument();
});

it("sends AI credentials only in a non-redirecting, non-cached Authorization header", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ artifact: null })));
  vi.stubGlobal("fetch", fetcher);
  await generateArtifact({ entityType: "repository", entityKey: "octo/alpha",
    artifactType: "repository_summary" }, token);
  const [url, init] = fetcher.mock.calls[0]!;
  expect(url).not.toContain(token);
  const query = new URL(url, "http://localhost").searchParams;
  expect(query.get("entity_type")).toBe("repository");
  expect(query.get("entity_key")).toBe("octo/alpha");
  expect(query.get("artifact_type")).toBe("repository_summary");
  expect(query.has("entityType")).toBe(false);
  expect(init).toMatchObject({ method: "POST", credentials: "omit", redirect: "error", cache: "no-store",
    headers: { Authorization: `Bearer ${token}` } });
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

it("clears the local AI token on pagehide and failed generation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.includes("/generate")) return Promise.resolve(new Response(JSON.stringify({
      error: { code: "workspace_unauthorized", message: "An admin token is required.", details: {} },
    }), { status: 401 }));
    return Promise.resolve(new Response(JSON.stringify(url.includes("/status")
      ? { configured: true, cached_artifacts: 0 } : { items: [] })));
  }));
  render(<MemoryRouter><AiPage /></MemoryRouter>);
  const input = screen.getByLabelText("Local admin token");
  fireEvent.change(input, { target: { value: token } });
  fireEvent(window, new Event("pagehide"));
  expect(input).toHaveValue("");
  fireEvent.change(input, { target: { value: token } });
  fireEvent.change(screen.getByLabelText("Entity type"), { target: { value: "ecosystem" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  await screen.findByText("An admin token is required.");
  expect(input).toHaveValue("");
});

it("passes the bridge form's two topics using the API contract", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(
    new Response(JSON.stringify({ artifact: null })),
  ));
  vi.stubGlobal("fetch", fetcher);
  await generateArtifact({ entityType: "bridge", entityKey: "mcp ai" }, token);
  const query = new URL(fetcher.mock.calls[0]![0], "http://localhost").searchParams;
  expect(query.get("topic_a")).toBe("mcp");
  expect(query.get("topic_b")).toBe("ai");
});
