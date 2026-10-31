import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  normalizeTurns,
  request,
  streamReply,
  type ReplyStreamEvent,
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

describe("streaming replies", () => {
  const session = {
    session_id: "s1",
    session_token: "capability",
    identity_status: "identified",
  };
  const result = {
    session_id: "s1",
    reply: "Xin chào. Hoàn tất.",
    used_fact_ids: [],
    guard_events: [],
  };
  function stubStream(body: string) {
    const bytes = new TextEncoder().encode(body);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let index = 0; index < bytes.length; index += 3)
          controller.enqueue(bytes.slice(index, index + 3));
        controller.close();
      },
    });
    const fetch = vi.fn().mockResolvedValue(
      new Response(stream, {
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }
  it("decodes fragmented Unicode and CRLF snapshots before the terminal receipt", async () => {
    const fetch = stubStream(
      `: heartbeat\r\n\r\nevent: status\r\ndata: {"stage":"drafting"}\r\n\r\nevent: text\r\ndata: {"text":"Xin chào."}\r\n\r\nevent: result\r\ndata: ${JSON.stringify(result)}\r\n\r\n`,
    );
    const received: ReplyStreamEvent[] = [];
    expect(
      await streamReply(session, "Chào", "same-id", (event) =>
        received.push(event),
      ),
    ).toEqual(result);
    expect(received).toEqual([
      { type: "status", stage: "drafting" },
      { type: "text", text: "Xin chào." },
    ]);
    const options = fetch.mock.calls[0][1];
    expect(options.headers.Authorization).toBe("Bearer capability");
    expect(JSON.parse(options.body)).toEqual({
      text: "Chào",
      client_message_id: "same-id",
    });
  });
  it("delivers reset snapshots when the backup starts", async () => {
    stubStream(
      `event: text\ndata: {"text":"Bản nháp."}\n\nevent: text\ndata: {"text":""}\n\nevent: result\ndata: ${JSON.stringify(result)}\n\n`,
    );
    const received: ReplyStreamEvent[] = [];
    await streamReply(session, "Chào", "id", (event) => received.push(event));
    expect(received.at(-1)).toEqual({ type: "text", text: "" });
  });
  it.each([
    'event: text\ndata: {"text":"Chưa xong"}\n\n',
    "event: text\ndata: invalid-json\n\n",
  ])(
    "rejects incomplete or invalid streams with an actionable error",
    async (body) => {
      stubStream(body);
      await expect(
        streamReply(session, "Chào", "id", () => {}),
      ).rejects.toMatchObject({
        status: 502,
        message: expect.stringContaining("thử lại"),
      });
    },
  );
  it("preserves abort and localizes transport failures", async () => {
    const abort = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(streamReply(session, "Chào", "id", () => {})).rejects.toBe(
      abort,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(
      streamReply(session, "Chào", "id", () => {}),
    ).rejects.toMatchObject({ status: 0 });
  });
  it("handles terminal server errors and owner authentication", async () => {
    stubStream(
      'event: error\ndata: {"message":"Xin thử lại","status":409}\n\n',
    );
    await expect(
      streamReply(session, "Chào", "id", () => {}),
    ).rejects.toMatchObject({ status: 409 });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    await expect(
      streamReply(session, "Chào", "id", () => {}),
    ).rejects.toMatchObject({ status: 401 });
  });
});
