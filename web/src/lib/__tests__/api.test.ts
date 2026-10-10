import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api", () => {
  const fetchMock = vi.fn();

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("unwraps the uniform envelope on success", async () => {
    vi.stubGlobal("fetch", fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok" })));
    const result = await api<{ status: string }>("/health");
    expect(result).toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health", expect.objectContaining({}));
  });

  it("surfaces the typed error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMock.mockResolvedValue(
        jsonResponse(404, {
          error: { code: "not_found", message: "missing", details: {} },
        }),
      ),
    );
    const error = await api("/repositories/octo/absent").catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.code).toBe("not_found");
    expect(apiError.message).toBe("missing");
  });

  it("converts unparseable failures into a typed error", async () => {
    vi.stubGlobal("fetch", fetchMock.mockResolvedValue(new Response("<html>", { status: 500 })));
    const error = (await api("/health").catch((err: unknown) => err)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("unparseable");
  });
});