import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test(`separate conversations preserve drafts, names and server history at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const turns: Record<string, { id: string; text: string; reply: string }[]> = {};
    let starts = 0, siblings = 0, ends = 0;
    const create = () => {
      const id = `chat-${++starts}`;
      turns[id] = [];
      return { session_id: id, session_token: `token-${id}`, identity_status: "unverified", status: "active" };
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/demo/profiles") return route.fulfill({ json: { profiles: [{ id: "hoa", name: "Hoa" }] } });
      if (path === "/sessions") return route.fulfill({ json: create() });
      const match = path.match(/^\/sessions\/(chat-\d+)(?:\/(\w+)(?:\/stream)?)?$/);
      if (!match) return route.fulfill({ status: 404, json: {} });
      const [, id, action] = match;
      expect(route.request().headers().authorization).toBe(`Bearer token-${id}`);
      if (action === "new") { siblings++; return route.fulfill({ json: create() }); }
      if (action === "end") { ends++; return route.fulfill({ json: {} }); }
      if (action === "turns") return route.fulfill({ json: { items: turns[id] } });
      if (action === "messages") {
        const body = route.request().postDataJSON();
        const reply = `Trả lời riêng của ${id}`;
        turns[id].push({ id: body.client_message_id, text: body.text, reply });
        return route.fulfill({ contentType: "text/event-stream", body: `event: result\ndata: ${JSON.stringify({ session_id: id, reply, guard_events: [] })}\n\n` });
      }
      return route.fulfill({ json: { session_id: id, status: "active", identity_status: "unverified" } });
    });
    const history = width === 375 ? page.getByRole("dialog", { name: "Các cuộc trò chuyện" }) : page.locator("#customer-history");
    const openHistory = async () => { if (width === 375) await page.getByRole("button", { name: "Mở danh sách phiên" }).click(); };
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await page.goto("/chat");
    await page.getByRole("button", { name: "Bắt đầu", exact: true }).click();
    await composer.fill("Tư vấn vay mua nhà");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Trả lời riêng của chat-1");
    await composer.fill("Bản nháp về ngôi nhà");
    await page.getByRole("button", { name: "Bắt đầu phiên mới", exact: true }).click();
    await expect(composer).toHaveValue("");
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("log")).toHaveCount(0);
    await composer.fill("Tư vấn thẻ tín dụng");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Trả lời riêng của chat-2");
    await expect(page.getByRole("log")).not.toContainText("Tư vấn vay mua nhà");
    await composer.fill("Bản nháp về chiếc thẻ");
    await openHistory();
    await expect(history.locator(".history-row")).toHaveCount(2);
    await page.screenshot({ path: testInfo.outputPath(`chat-session-list-${width}.png`), fullPage: true, animations: "disabled" });
    await history.getByRole("button", { name: /Tư vấn vay mua nhà Có thể tiếp tục/ }).click();
    await expect(composer).toHaveValue("Bản nháp về ngôi nhà");
    await expect(page.getByRole("log")).toContainText("Trả lời riêng của chat-1");
    await expect(page.getByRole("log")).not.toContainText("Trả lời riêng của chat-2");
    await openHistory();
    await history.getByRole("button", { name: /Tư vấn thẻ tín dụng Có thể tiếp tục/ }).click();
    await expect(composer).toHaveValue("Bản nháp về chiếc thẻ");
    await openHistory();
    await history.getByRole("button", { name: "Đổi tên: Tư vấn thẻ tín dụng" }).click();
    await history.getByRole("textbox", { name: "Tên cuộc trò chuyện" }).fill("Kế hoạch mở thẻ");
    await history.getByRole("button", { name: "Lưu tên" }).click();
    await history.getByRole("textbox", { name: "Tìm cuộc trò chuyện" }).fill("mở thẻ");
    await expect(history.locator(".history-row")).toHaveCount(1);
    await history.getByRole("button", { name: /Kế hoạch mở thẻ Có thể tiếp tục/ }).click();
    await page.reload();
    await expect(page.getByRole("log")).toContainText("Trả lời riêng của chat-2");
    await expect(page.getByRole("heading", { name: "Kế hoạch mở thẻ", exact: true })).toBeVisible();
    await openHistory();
    await expect(history.locator(".history-row")).toHaveCount(2);
    if (width === 375) {
      await page.keyboard.press("Escape");
      await expect(history).not.toBeVisible();
      await expect(page.getByRole("button", { name: "Mở danh sách phiên" })).toBeFocused();
    }
    await page.screenshot({ path: testInfo.outputPath(`chat-session-active-${width}.png`), fullPage: true, animations: "disabled" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(starts).toBe(2); expect(siblings).toBe(1); expect(ends).toBe(0);
  });
}

test("a slow history load cannot replace the newly selected conversation", async ({ page }) => {
  let release!: () => void;
  const slow = new Promise<void>((resolve) => { release = resolve; });
  await page.addInitScript(() => {
    const entries = ["a", "b"].map((id) => ({ session: { session_id: id, session_token: `token-${id}`, identity_status: "unverified" }, title: `Phiên ${id}`, createdAt: "2026-10-31T00:00:00Z", updatedAt: "2026-10-31T00:00:00Z" }));
    sessionStorage.setItem("bankwise-chat-history-v1", JSON.stringify(entries));
    sessionStorage.setItem("bankwise-chat-session", JSON.stringify(entries[0].session));
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/demo/profiles")) return route.fulfill({ json: { profiles: [] } });
    if (path.endsWith("/a/turns")) { await slow; return route.fulfill({ json: { items: [{ id: "a", text: "Nội dung A", reply: "Trả lời A" }] } }); }
    if (path.endsWith("/b/turns")) return route.fulfill({ json: { items: [{ id: "b", text: "Nội dung B", reply: "Trả lời B" }] } });
    return route.fulfill({ json: { status: "active" } });
  });
  await page.goto("/chat#conversation");
  await page.locator("#customer-history").getByRole("button", { name: /Phiên b Có thể tiếp tục/ }).click();
  await expect(page.getByRole("log")).toContainText("Nội dung B");
  release();
  await expect(page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" })).toBeEnabled();
  await expect(page.getByRole("log")).not.toContainText("Nội dung A");
});
