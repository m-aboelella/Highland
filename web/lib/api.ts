export const API_BASE_URL =
  process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

export type ApiErrorPayload = { detail?: unknown };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  fallbackMessage = "The request could not be completed.",
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(apiUrl(path), { ...init, headers });
  const payload = response.status === 204
    ? undefined
    : typeof response.json === "function"
      ? await response.json().catch(() => undefined) as unknown
      : undefined;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as ApiErrorPayload).detail
      : undefined;
    throw new ApiError(
      typeof detail === "string" && detail.trim() ? detail : fallbackMessage,
      response.status,
      payload,
    );
  }
  return payload as T;
}
