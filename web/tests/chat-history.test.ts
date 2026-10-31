import { describe, expect, it } from "vitest";
import { CHAT_HISTORY, CURRENT_SESSION, readChatHistory } from "../src/chat-history";
const session = { session_id: "one", session_token: "capability", identity_status: "unverified" };
const store = (items: Record<string, string>) => ({ getItem: (key: string) => items[key] ?? null });
describe("conversation index", () => {
  it("migrates the previous single-session storage without losing access", () => {
    const result = readChatHistory(store({ "mach-chat-session": JSON.stringify(session) }));
    expect(result.current).toEqual(session);
    expect(result.entries[0].session).toEqual(session);
  });
  it("ignores malformed sessions and duplicate index records", () => {
    const result = readChatHistory(store({ [CURRENT_SESSION]: "[]", [CHAT_HISTORY]: JSON.stringify([
      null, { session: {} }, { session, title: "One" }, { session, title: "Duplicate" },
    ]) }));
    expect(result.current).toBeNull();
    expect(result.entries.map((entry) => entry.title)).toEqual(["One"]);
  });
  it("retains the current session even when the index is corrupted", () => {
    const result = readChatHistory(store({ [CURRENT_SESSION]: JSON.stringify(session), [CHAT_HISTORY]: "broken-json" }));
    expect(result.entries).toHaveLength(1);
    expect(result.current).toEqual(session);
  });
});
