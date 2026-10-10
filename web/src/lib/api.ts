/** Uniform error envelope returned by the FastAPI backend. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error.code;
    this.details = body.error.details;
  }
}

/**
 * Fetch wrapper for the `/api/v1` surface. Unwraps the uniform error
 * envelope, so every failure surfaces as a typed {@link ApiError}.
 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Accept: "application/json" },
    ...init,
  });

  if (!response.ok) {
    let body: ApiErrorBody;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      throw new ApiError(response.status, {
        error: {
          code: "unparseable",
          message: `Server returned HTTP ${response.status}`,
          details: {},
        },
      });
    }
    throw new ApiError(response.status, body);
  }
  return (await response.json()) as T;
}