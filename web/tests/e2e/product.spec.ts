import { expect, test, type Page } from "@playwright/test";

async function mockAgentChat(page: Page, failFirstMessage = false) {
  const state = {
    starts: 0,
    claimed: null as string | null,
    messageIds: [] as string[],
    turns: [] as { id: string; text: string; reply: string }[],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const body = route.request().postDataJSON();
    if (path === "/sessions") {
      expect(body).toEqual({ mode: "harness" });
      state.starts += 1;
      return route.fulfill({
        json: {
          session_id: "anonymous-session",
          session_token: "anonymous-capability",
          user_id: null,
          identity_status: "unverified",
          brief: { redacted: true },
        },
      });
    }
    if (path.endsWith("/turns"))
      return route.fulfill({ json: { items: state.turns } });
    if (path.endsWith("/messages")) {
      expect(route.request().headers().authorization).toBe(
        "Bearer anonymous-capability",
      );
      state.messageIds.push(body.client_message_id);
      if (failFirstMessage && state.messageIds.length === 1)
        return route.fulfill({
          status: 503,
          json: { detail: "Dịch vụ tạm thời chưa sẵn sàng." },
        });
      let reply =
        "Tôi có thể giải thích thông tin vay mua nhà công khai trước xác minh.";
      if (body.text === "0900000002") {
        state.claimed = "0900000002";
        reply =
          "Đã xác minh hồ sơ Trần Minh An từ kết quả verify_identity cho phiên này.";
      } else if (body.text === "0999999999") {
        reply =
          "Chưa tìm thấy hồ sơ phù hợp với số điện thoại này. Bạn vẫn có thể hỏi thông tin công khai.";
      }
      state.turns.push({ id: body.client_message_id, text: body.text, reply });
      return route.fulfill({
        json: {
          session_id: "anonymous-session",
          user_id: state.claimed,
          identity_status: state.claimed ? "verified" : "unverified",
          brief: state.claimed
            ? { customer_id: "minh", name: "Trần Minh An", needs: [] }
            : { redacted: true },
          reply,
          used_fact_ids: [],
          guard_events: [],
        },
      });
    }
    return route.fulfill({
      status: 404,
      json: { detail: "Unexpected request" },
    });
  });
  return state;
}

test("the first chat message opens an anonymous session and uses the phone-2 verification result", async ({
  page,
}, testInfo) => {
  const state = await mockAgentChat(page);
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
  await expect(composer).toBeEnabled();
  await expect(
    page.locator(
      'select, input[type="date"], #dob, .identity-help, .support-session, .phone-context',
    ),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Tiếp tục bằng số điện thoại" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Gặp chuyên viên", exact: true }),
  ).toBeDisabled();
  expect(state.starts).toBe(0);
  await composer.fill("0900000002");
  await composer.evaluate((element) => {
    const form = element.closest("form");
    if (!form) throw new Error("Missing chat composer form");
    form.requestSubmit();
    form.requestSubmit();
  });
  await expect(page).toHaveURL(/#conversation$/);
  await expect(page.getByRole("log")).toContainText("Trần Minh An");
  await expect(page.getByRole("log")).not.toContainText("Nguyễn Thị Hoa");
  expect(state.starts).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("bankwise-chat-session") ?? "null")
            ?.user_id,
      ),
    )
    .toBe("0900000002");
  await expect(
    page.getByRole("button", { name: "Gặp chuyên viên", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: testInfo.outputPath("chat-phone-2.png"),
    fullPage: true,
  });
});

test("an unknown phone keeps the anonymous session and public chat available", async ({
  page,
}) => {
  const state = await mockAgentChat(page);
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
  await composer.fill("0999999999");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("log")).toContainText("Chưa tìm thấy hồ sơ");
  await expect(
    page.getByRole("button", { name: "Gặp chuyên viên", exact: true }),
  ).toBeDisabled();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("bankwise-chat-session") ?? "null")
            ?.identity_status,
      ),
    )
    .toBe("unverified");
  await composer.fill("Tôi muốn tìm hiểu vay mua nhà.");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("log")).toContainText("công khai trước xác minh");
  expect(state.starts).toBe(1);
  expect(state.claimed).toBeNull();
});

test("a suggestion sends directly; retry preserves the anonymous session and message id", async ({
  page,
}, testInfo) => {
  const state = await mockAgentChat(page, true);
  await page.goto("/chat");
  await page.getByRole("button", { name: /Vay mua nhà/ }).click();
  await expect(page).toHaveURL(/#conversation$/);
  await expect(page.getByRole("alert")).toContainText("Dịch vụ tạm thời");
  expect(state.starts).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("bankwise-chat-session") ?? "null")
            ?.session_id,
      ),
    )
    .toBe("anonymous-session");
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.getByRole("log")).toContainText("công khai trước xác minh");
  expect(state.messageIds).toHaveLength(2);
  expect(state.messageIds[0]).toBe(state.messageIds[1]);
  expect(state.starts).toBe(1);
  expect(state.claimed).toBeNull();
  await page.screenshot({
    path: testInfo.outputPath("chat-anonymous-retry.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("staff auth gates private content and incomplete benchmark is not presented as complete", async ({
  page,
}) => {
  let loggedIn = false;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    if (path === "/staff/me")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? { authenticated: true, csrf_token: "test-csrf" }
          : { detail: "Unauthorized" },
      });
    if (path === "/staff/login") {
      loggedIn = true;
      return route.fulfill({ json: { csrf_token: "test-csrf" } });
    }
    if (path === "/eval/runs")
      return route.fulfill({
        json: {
          schema_version: 1,
          runs: [{ run_id: "run-test", status: "incomplete", split: "dev" }],
        },
      });
    if (path === "/eval/runs/run-test")
      return route.fulfill({
        json: {
          schema_version: 1,
          run_id: "run-test",
          status: "incomplete",
          split: "dev",
          summary: { harness: { price_error_rate: 0 } },
          judge: { status: "skipped" },
        },
      });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/benchmark");
  await expect(
    page.getByRole("heading", { name: "Chào mừng trở lại" }),
  ).toBeVisible();
  await expect(
    page.getByText("Kết quả thực nghiệm", { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Mật khẩu chuyên viên").fill("test-password");
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(
    page.getByText("Lượt đánh giá còn thiếu kết quả.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("LLM judge: Chưa chạy")).toBeVisible();
});

for (const [width, height] of [
  [375, 812],
  [768, 1024],
  [1440, 900],
])
  test(`chat layout fits ${width}px and captures designed empty state`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height });
    await page.goto("/chat");
    await expect(
      page.getByRole("heading", {
        name: "Chúng tôi có thể hỗ trợ gì cho bạn?",
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Dành cho chuyên viên" }),
    ).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({
      path: testInfo.outputPath(`chat-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(
      await page
        .locator(".conversation-scroll")
        .evaluate((element) => element.scrollHeight <= element.clientHeight),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const toggle = page.getByRole("button", { name: /thông tin hỗ trợ/ });
    const wasExpanded = (await toggle.getAttribute("aria-expanded")) === "true";
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", String(!wasExpanded));
    if (wasExpanded)
      await expect(
        page.getByRole("complementary", { name: "Thông tin hỗ trợ" }),
      ).toBeHidden();
    else
      await expect(
        page.getByRole("complementary", { name: "Thông tin hỗ trợ" }),
      ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`chat-context-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  });

test("staff queue uses real counts and supports search with reset", async ({
  page,
}, testInfo) => {
  const handoffs = [
    {
      handoff_id: "h1",
      customer_id: "hoa",
      reason: "Tư vấn hồ sơ vay mua nhà",
      status: "pending",
    },
    {
      handoff_id: "h2",
      customer_id: "minh",
      reason: "Hướng dẫn mở thẻ tín dụng",
      status: "completed",
    },
  ];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    if (path === "/staff/me")
      return route.fulfill({ json: { csrf_token: "test-csrf" } });
    if (path === "/handoffs")
      return route.fulfill({ json: { items: handoffs } });
    if (path === "/callbacks") return route.fulfill({ json: { items: [] } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/staff");
  await expect(
    page.getByRole("button", { name: /Tư vấn hồ sơ vay mua nhà/ }),
  ).toBeVisible();
  await expect(page.getByLabel("Tổng quan công việc")).toContainText("1");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: testInfo.outputPath("staff-desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("searchbox", { name: "Tìm yêu cầu hỗ trợ" }).fill("hoa");
  await expect(
    page.getByRole("button", { name: /Tư vấn hồ sơ vay mua nhà/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Hướng dẫn mở thẻ/ }),
  ).toHaveCount(0);
  await page
    .getByRole("searchbox", { name: "Tìm yêu cầu hỗ trợ" })
    .fill("không tồn tại");
  await expect(
    page.getByRole("heading", { name: "Chưa tìm thấy yêu cầu" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Xóa bộ lọc" }).click();
  await expect(
    page.getByRole("button", { name: /Hướng dẫn mở thẻ/ }),
  ).toBeVisible();
});

test("staff login preview and reduced motion preference", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/api/staff/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Unauthorized" } }),
  );
  await page.goto("/staff");
  await expect(
    page.getByRole("heading", { name: "Chào mừng trở lại" }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: testInfo.outputPath("staff-login.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.goto("/chat");
  await expect(page.locator(".illustrated-card")).toHaveCSS(
    "animation-name",
    "none",
  );
});
