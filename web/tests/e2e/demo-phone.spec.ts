import { expect, test } from "@playwright/test";

for (const demoPhoneIdentity of [false, true]) for (const width of [375, 1440]) {
  test(`phone entry opens a greeted conversation at ${width}px (demo=${demoPhoneIdentity})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let starts = 0, messages = 0, siblings = 0;
    const phones: Record<string, string> = {};
    const turns: Record<string, { id: string; text: string; reply: string }[]> = {};
    const session = (id: string) => ({
      session_id: id, session_token: `token-${id}`, identity_status: "identified", status: "active", telegram_url: "https://t.me/test_bank_bot",
      user_id: phones[id], brief: { customer_id: phones[id], name: "Khách demo", needs: [] },
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/demo/profiles")
        return route.fulfill({ json: { demo_phone_identity: demoPhoneIdentity, profiles: [] } });
      if (path === "/auth/phone/login") {
        starts++;
        if (route.request().postDataJSON().phone === "123")
          return route.fulfill({ status: 422, json: { detail: "Số điện thoại không hợp lệ." } });
        const id = `chat-${starts}`;
        phones[id] = route.request().postDataJSON().phone;
        turns[id] = [{ id: `opening-${id}`, text: "", reply: "Chào anh/chị! Em là trợ lý Bankwise. Anh/chị cần hỗ trợ gì hôm nay?" }];
        return route.fulfill({ json: session(id) });
      }
      if (path.endsWith("/new")) { siblings++; return route.fulfill({ json: session("second") }); }
      if (path.endsWith("/messages/stream")) {
        messages++;
        const body = route.request().postDataJSON();
        turns[path.split("/")[2]].push({ id: body.client_message_id, text: body.text, reply: "Đã ghi nhớ" });
        return route.fulfill({ contentType: "text/event-stream", body: `event: result\ndata: ${JSON.stringify({ session_id: path.split("/")[2], reply: "Đã ghi nhớ", guard_events: [] })}\n\n` });
      }
      if (path.endsWith("/turns")) return route.fulfill({ json: { items: turns[path.split("/")[2]] } });
      if (path.startsWith("/sessions/")) return route.fulfill({ json: session(path.split("/")[2]) });
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto("/chat");
    const phone = page.getByLabel("Số điện thoại của bạn", { exact: true });
    const start = page.getByRole("button", { name: "Bắt đầu chat", exact: true });
    await expect(phone).toBeVisible();
    expect((await phone.boundingBox())!.y).toBeLessThan(450);
    await expect(start).toBeDisabled();
    await expect(page.getByLabel("Chọn hồ sơ để bắt đầu hỗ trợ")).toHaveCount(0);
    await phone.fill("0901234567");
    await page.screenshot({ path: testInfo.outputPath(`demo-phone-entry-${width}.png`), fullPage: true, animations: "disabled" });
    await phone.fill("123");
    await phone.press("Enter");
    await expect(page.getByText("Số điện thoại không hợp lệ.", { exact: true })).toBeVisible();
    await expect(phone).toHaveValue("123");
    if (demoPhoneIdentity) {
      await page.locator(".suggestion-0").click();
      await expect(phone).toBeFocused();
      expect(messages).toBe(0);
    }
    await phone.fill("0901234567");
    await phone.press("Enter");
    await expect(phone).toHaveCount(0);
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    if (demoPhoneIdentity) await expect(composer).toHaveValue("Tôi muốn tìm hiểu vay mua nhà.");
    await expect(page.getByRole("log")).toContainText("Em là trợ lý Bankwise");
    await expect(page.getByRole("log").locator(".from-user")).toHaveCount(0);
    expect(messages).toBe(0); // Phone entry is not a conversation message.
    await composer.fill("Tư vấn vay mua nhà");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Đã ghi nhớ");
    await composer.fill("Bản nháp của số cũ");
    await page.getByRole("button", { name: "Bắt đầu phiên mới", exact: true }).click();
    await expect(phone).toBeVisible();
    await expect(phone).toHaveValue("");
    await expect(phone).toBeFocused();
    await expect(start).toBeDisabled();
    expect(starts).toBe(2);
    expect(siblings).toBe(0);

    // Selecting a new number does not discard the previous conversation or draft.
    const history = width === 375 ? page.getByRole("dialog", { name: "Các cuộc trò chuyện" }) : page.locator("#customer-history");
    const openHistory = async () => { if (width === 375) await page.getByRole("button", { name: "Mở danh sách phiên" }).click(); };
    await openHistory();
    await history.getByRole("button", { name: /Tư vấn vay mua nhà Có thể tiếp tục/ }).click();
    await expect(composer).toHaveValue("Bản nháp của số cũ");
    await expect(page.getByRole("log")).toContainText("Đã ghi nhớ");
    await openHistory();
    await history.getByRole("button", { name: "Cuộc trò chuyện mới", exact: true }).click();
    await expect(phone).toBeFocused();
    await page.reload();
    await expect(phone).toHaveValue("");
    await phone.fill("0912345678");
    await start.click();
    await expect(phone).toHaveCount(0);
    await expect(page.getByRole("log").locator(".from-assistant")).toHaveCount(1);
    await expect(composer).toHaveValue("");
    await expect(composer).toBeEnabled();
    expect(phones["chat-3"]).toBe("0912345678");
    expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("bankwise-chat-session")!).user_id)).toBe("0912345678");
    await page.reload();
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("log").locator(".from-assistant")).toHaveCount(1);
    await openHistory();
    await expect(history.locator(".history-row")).toHaveCount(2);
    await history.getByRole("button", { name: /Tư vấn vay mua nhà Có thể tiếp tục/ }).click();
    await expect(page.getByRole("log")).toContainText("Đã ghi nhớ");
    expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("bankwise-chat-session")!).user_id)).toBe("0901234567");

    // Reusing a number is also allowed, but only after explicit entry.
    await page.getByRole("button", { name: "Bắt đầu phiên mới", exact: true }).click();
    await phone.fill("0901234567");
    await start.click();
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("log").locator(".from-assistant")).toHaveCount(1);
    expect(phones["chat-4"]).toBe("0901234567");
    expect(starts).toBe(4);
    expect(siblings).toBe(0);
    expect(messages).toBe(1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
  });
}
