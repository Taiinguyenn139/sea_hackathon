import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createChatSession,
  normalizeTurns,
  request,
} from "../src/api";
afterEach(() => vi.unstubAllGlobals());
describe("API boundary", () => {
  it("sends bearer only when a customer capability is supplied and includes CSRF for staff writes", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true })));
    vi.stubGlobal("fetch", fetch);
    await request("/handoffs/h1/complete", {
      method: "POST",
      body: { notes: "done" },
      csrf: "csrf-value",
    });
    const options = fetch.mock.calls[0][1];
    expect(options.credentials).toBe("include");
    expect(options.headers["X-CSRF-Token"]).toBe("csrf-value");
    expect(options.headers.Authorization).toBeUndefined();
  });
  it("creates a neutral chat session without selecting a customer or sending credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: "anonymous",
          session_token: "capability",
          user_id: null,
          identity_status: "unverified",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const session = await createChatSession();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toMatch(/\/sessions$/);
    expect(JSON.parse(options.body)).toEqual({ mode: "harness" });
    expect(options.headers.Authorization).toBeUndefined();
    expect(session.user_id).toBeNull();
    expect(session.identity_status).toBe("unverified");
  });
  it("reports authorization failures without treating error JSON as successful data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Session expired" }), {
          status: 401,
        }),
      ),
    );
    await expect(request("/sessions/x")).rejects.toMatchObject({
      status: 401,
      message: "Session expired",
    });
  });
  it("keeps transport failures actionable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(request("/demo/profiles")).rejects.toBeInstanceOf(ApiError);
  });
  it("rejects non-JSON success responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>wrong endpoint</html>")),
    );
    await expect(request("/demo/profiles")).rejects.toThrow("không hợp lệ");
  });
  it("restores both messages from persisted server turn pairs", () => {
    expect(
      normalizeTurns({
        items: [
          { id: "turn1", text: "Vay mua nhà", reply: "Mình có thể giúp bạn." },
        ],
      }),
    ).toEqual([
      {
        id: "turn1-user",
        role: "user",
        text: "Vay mua nhà",
        created_at: undefined,
      },
      {
        id: "turn1-assistant",
        role: "assistant",
        text: "Mình có thể giúp bạn.",
        created_at: undefined,
      },
    ]);
  });
});
