import { expect, test } from "@playwright/test";

for (const width of [375, 768, 1440]) {
  test(`anonymous chat, home, draft, reload and new chat work at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let starts = 0;
    let ends = 0;
    let siblings = 0;
    let verifications = 0;
    const turns = new Map<
      string,
      { id: string; text: string; reply: string }[]
    >();
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/sessions" || path.endsWith("/new")) {
        if (path === "/sessions")
          expect(route.request().postDataJSON()).toEqual({ mode: "harness" });
        else siblings += 1;
        starts += 1;
        const sessionId = `navigation-session-${starts}`;
        turns.set(sessionId, []);
        return route.fulfill({
          json: {
            session_id: sessionId,
            session_token: "navigation-capability",
            user_id: null,
            identity_status: "unverified",
            brief: { redacted: true },
          },
        });
      }
      if (path.endsWith("/turns")) return route.fulfill({ json: { items: turns } });
      if (path.endsWith("/messages/stream")) {
        expect(route.request().headers().authorization).toBe("Bearer navigation-capability");
        const body = route.request().postDataJSON();
        const reply = "Bạn có thể hỏi điều kiện và hồ sơ vay mua nhà ngay tại đây.";
        turns.push({ id: body.client_message_id, text: body.text, reply });
        return route.fulfill({ contentType: "text/event-stream", body: `event: result\ndata: ${JSON.stringify({ session_id: "navigation-session", identity_status: "unverified", reply })}\n\n` });
      }
      if (path.endsWith("/verify")) verifications += 1;
      if (path.endsWith("/end")) {
        ends += 1;
        return route.fulfill({ json: { status: "ended" } });
      }
      return route.fulfill({
        status: 404,
        json: { detail: "Unexpected request" },
      });
    });
    await page.goto("/chat");
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    await expect(page.locator('select, input[type="date"], #dob')).toHaveCount(
      0,
    );
    expect(starts).toBe(0);
    await composer.fill("Điều kiện vay mua nhà là gì?");
    await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
    await expect(page).toHaveURL(/#conversation$/);
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
    await expect(page.getByRole("log")).toContainText(
      "Tôi muốn tìm hiểu thẻ tín dụng.",
    );
    await expect(composer).toHaveValue("");
    if (width === 1440) {
      await page
        .getByRole("link", { name: "Ngân hàng Bankwise — hỗ trợ khách hàng" })
        .click();
      await expect(home).toBeVisible();
      await page
        .getByRole("button", { name: "Tiếp tục trò chuyện", exact: true })
        .click();
    }
    await composer.fill("Tôi còn muốn hỏi về phí");
    await page.reload();
    await expect(page.getByRole("log")).toContainText(
      "Điều kiện vay mua nhà là gì?",
    );
    await expect(page.getByRole("log")).toContainText(
      "Tôi muốn tìm hiểu thẻ tín dụng.",
    );
    await expect(composer).toHaveValue("Tôi còn muốn hỏi về phí");
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
    await page
      .getByRole("button", { name: "Bắt đầu phiên mới", exact: true })
      .click();
    await expect(page).toHaveURL(/#conversation$/);
    await expect(page.getByRole("log")).toHaveCount(0);
    await expect(composer).toHaveValue("");
    await expect(composer).toBeEnabled();
    await composer.fill("Tôi muốn tìm hiểu vay tiêu dùng.");
    await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
    await expect(page.getByRole("log")).toContainText(
      "Tôi muốn tìm hiểu vay tiêu dùng.",
    );
    await expect(page.getByRole("log")).not.toContainText(
      "Điều kiện vay mua nhà là gì?",
    );
    expect(starts).toBe(2);
    expect(ends).toBe(0);
    expect(siblings).toBe(1);
    expect(verifications).toBe(0);
  });
}

test("new conversations leave a waiting staff handoff available in history", async ({
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
    if (path.endsWith("/new"))
      return route.fulfill({
        json: {
          session_id: "new-session",
          session_token: "new-capability",
          user_id: null,
          identity_status: "unverified",
          status: "active",
        },
      });
    if (path.endsWith("/turns"))
      return route.fulfill({
        json: {
          items: path.includes("waiting-session")
            ? [
                {
                  id: "saved",
                  text: "Tôi muốn gặp chuyên viên",
                  reply: "Đã gửi yêu cầu.",
                },
              ]
            : [],
        },
      });
    if (path.endsWith("/end")) {
      ends += 1;
      return route.fulfill({
        status: 409,
        json: { detail: "Complete handoff before ending session" },
      });
    }
    return route.fulfill({
      json: {
        status: path.includes("waiting-session") ? "waiting_human" : "active",
      },
    });
  });
  await page.goto("/chat#conversation");
  await expect(page.getByRole("log")).toContainText("Đã gửi yêu cầu.");
  await page.getByRole("button", { name: "Bắt đầu phiên mới" }).click();
  await expect(
    page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }),
  ).toBeEnabled();
  await page
    .locator("#customer-history")
    .getByRole("button", { name: /Tôi muốn gặp chuyên viên Chờ chuyên viên/ })
    .click();
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
  expect(ends).toBe(0);
});
