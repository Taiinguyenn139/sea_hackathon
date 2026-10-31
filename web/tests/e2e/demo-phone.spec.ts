import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test(`demo phone box starts a clean conversation at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let starts = 0, messages = 0, siblings = 0;
    const session = (id: string) => ({
      session_id: id, session_token: `token-${id}`, identity_status: "identified", status: "active",
      user_id: "+84901234567", brief: { customer_id: "same-customer", name: "Khách demo", needs: [] },
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/demo/profiles")
        return route.fulfill({ json: { demo_phone_identity: true, profiles: [{ id: "hoa", name: "Hoa" }] } });
      if (path === "/demo/phone/session") {
        starts++;
        if (route.request().postDataJSON().phone === "123")
          return route.fulfill({ status: 422, json: { detail: "Số điện thoại không hợp lệ." } });
        expect(route.request().postDataJSON().phone).toBe("0901234567");
        return route.fulfill({ json: session("first") });
      }
      if (path.endsWith("/new")) { siblings++; return route.fulfill({ json: session("second") }); }
      if (path.endsWith("/messages/stream")) {
        messages++;
        return route.fulfill({ contentType: "text/event-stream", body: `event: result\ndata: ${JSON.stringify({ session_id: path.split("/")[2], reply: "Đã ghi nhớ", guard_events: [] })}\n\n` });
      }
      if (path.endsWith("/turns")) return route.fulfill({ json: { items: [] } });
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
    await phone.fill("0901234567");
    await phone.press("Enter");
    await expect(phone).toHaveCount(0);
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("log")).toHaveCount(0);
    expect(messages).toBe(0); // Phone entry is not a conversation message.
    await composer.fill("Tư vấn vay mua nhà");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Đã ghi nhớ");
    await page.getByRole("button", { name: "Bắt đầu phiên mới", exact: true }).click();
    await expect(page.getByRole("log")).toHaveCount(0);
    await expect(composer).toHaveValue("");
    await expect(composer).toBeEnabled();
    expect(starts).toBe(2); // Only invalid entry + valid entry; sibling uses the same identity.
    expect(siblings).toBe(1);
    expect(messages).toBe(1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
  });
}
