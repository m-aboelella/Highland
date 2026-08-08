import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, requestJson } from "./api";

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("builds URLs against the configured Highland API", () => {
    expect(apiUrl("/runs")).toBe("http://127.0.0.1:8080/runs");
  });

  it("adds JSON headers and returns typed payloads", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "run-1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetch);

    const result = await requestJson<{ id: string }>("/runs", {
      method: "POST",
      body: JSON.stringify({ prompt: "hello" }),
    });

    expect(result.id).toBe("run-1");
    expect(new Headers(fetch.mock.calls[0][1].headers).get("Content-Type")).toBe(
      "application/json",
    );
  });

  it("normalizes API error details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Run not found" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    )));

    await expect(requestJson("/runs/missing")).rejects.toMatchObject({
      message: "Run not found",
      name: "ApiError",
      status: 404,
    });
  });
});
