import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test(`chat starts without date of birth and home preserves the conversation at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let starts = 0;
    let ends = 0;
    let verifications = 0;
    const turns: { id: string; text: string; reply: string }[] = [];
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/demo/profiles")
        return route.fulfill({
          json: {
            profiles: [
              { id: "0900000001", name: "Nguyễn Thị Hoa", phone: "0900000001" },
            ],
          },
        });
      if (path === "/sessions") {
        starts += 1;
        return route.fulfill({
          json: {
            session_id: "navigation-session",
            session_token: "navigation-capability",
            identity_status: "unverified",
            brief: { redacted: true },
          },
        });
      }
      if (path.endsWith("/turns"))
        return route.fulfill({ json: { items: turns } });
      if (path.endsWith("/messages/stream")) {
        expect(route.request().headers().authorization).toBe(
          "Bearer navigation-capability",
        );
        const body = route.request().postDataJSON();
        const reply =
          "Bạn có thể hỏi điều kiện và hồ sơ vay mua nhà ngay tại đây.";
        turns.push({ id: body.client_message_id, text: body.text, reply });
        return route.fulfill({
          contentType: "text/event-stream",
          body: `event: result\ndata: ${JSON.stringify({ session_id: "navigation-session", identity_status: "unverified", reply })}\n\n`,
        });
      }
      if (path.endsWith("/verify")) verifications += 1;
      if (path.endsWith("/end")) ends += 1;
      return route.fulfill({
        status: 404,
        json: { detail: "Unexpected request" },
      });
    });
    await page.goto("/chat");
    await page.getByRole("button", { name: "Bắt đầu", exact: true }).click();
    await expect(page).toHaveURL(/#conversation$/);
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    await expect(page.locator('input[type="date"], #dob')).toHaveCount(0);
    await composer.fill("Điều kiện vay mua nhà là gì?");
    await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
    await expect(page.getByRole("log")).toContainText("hồ sơ vay mua nhà ngay");
    await composer.fill("Tôi đang chuẩn bị giấy tờ");
    await page.getByRole("link", { name: "Về màn hình chính" }).click();
    await expect(page).toHaveURL(/#home$/);
    const home = page.getByRole("heading", {
      name: "Chúng tôi có thể hỗ trợ gì cho bạn?",
    });
    await expect(home).toBeVisible();
    await expect(page.getByRole("log")).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`home-resume-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await page.goBack();
    await expect(composer).toHaveValue("Tôi đang chuẩn bị giấy tờ");
    await expect(page.getByRole("log")).toContainText(
      "Điều kiện vay mua nhà là gì?",
    );
    await page.goForward();
    await expect(home).toBeVisible();
    await page
      .getByRole("button", { name: "Tiếp tục trò chuyện", exact: true })
      .click();
    await expect(composer).toHaveValue("Tôi đang chuẩn bị giấy tờ");
    await page.getByRole("link", { name: "Về màn hình chính" }).click();
    await page.getByRole("button", { name: /Thẻ tín dụng/ }).click();
    await expect(composer).toHaveValue("Tôi muốn tìm hiểu thẻ tín dụng.");
    await expect(page.getByRole("log")).toContainText(
      "Điều kiện vay mua nhà là gì?",
    );
    if (width === 1440) {
      await page
        .getByRole("link", { name: "Ngân hàng Bankwise — hỗ trợ khách hàng" })
        .click();
      await expect(home).toBeVisible();
      await page
        .getByRole("button", { name: "Tiếp tục trò chuyện", exact: true })
        .click();
      await expect(composer).toHaveValue("Tôi muốn tìm hiểu thẻ tín dụng.");
    }
    await page.reload();
    await expect(page.getByRole("log")).toContainText(
      "Điều kiện vay mua nhà là gì?",
    );
    await page.screenshot({
      path: testInfo.outputPath(`chat-navigation-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(starts).toBe(1);
    expect(ends).toBe(0);
    expect(verifications).toBe(0);
  });
}

test("home stays available when ending a session is blocked by a staff handoff", async ({
  page,
}) => {
  let ends = 0;
  await page.addInitScript(() =>
    sessionStorage.setItem(
      "bankwise-chat-session",
      JSON.stringify({
        session_id: "waiting-session",
        session_token: "waiting-capability",
        identity_status: "verified",
        handoff_id: "handoff-1",
        status: "waiting_human",
        demo_profile: "0900000001",
      }),
    ),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    if (path === "/demo/profiles")
      return route.fulfill({
        json: { profiles: [{ id: "0900000001", name: "Hoa" }] },
      });
    if (path.endsWith("/turns"))
      return route.fulfill({
        json: {
          items: [
            {
              id: "saved",
              text: "Tôi muốn gặp chuyên viên",
              reply: "Đã gửi yêu cầu.",
            },
          ],
        },
      });
    if (path.endsWith("/end")) {
      ends += 1;
      return route.fulfill({
        status: 409,
        json: { detail: "Complete handoff before ending session" },
      });
    }
    return route.fulfill({ json: { status: "waiting_human" } });
  });
  await page.goto("/chat#conversation");
  await page.getByRole("button", { name: "Bắt đầu phiên mới" }).click();
  await expect(page.getByRole("alert")).toContainText("đợi chuyên viên");
  await page.getByRole("link", { name: "Về màn hình chính" }).click();
  await expect(
    page.getByRole("heading", { name: "Chúng tôi có thể hỗ trợ gì cho bạn?" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Tiếp tục trò chuyện", exact: true })
    .click();
  await expect(page.getByRole("log")).toContainText("Đã gửi yêu cầu.");
  await expect(
    page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }),
  ).toBeDisabled();
  expect(ends).toBe(1);
});
