import { expect, test, type Page, type Route } from "@playwright/test";

async function fulfillApi(
  route: Route,
  options: Parameters<Route["fulfill"]>[0],
) {
  if (
    route.request().url().endsWith("/messages/stream") &&
    options.json &&
    (options.status ?? 200) < 400
  )
    return route.fulfill({
      ...options,
      json: undefined,
      contentType: "text/event-stream",
      body: `event: result\ndata: ${JSON.stringify(options.json)}\n\n`,
    });
  return route.fulfill(options);
}

test("chat shows partial text while the streamed reply is still in progress", async ({
  page,
}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "bankwise-chat-session",
      JSON.stringify({
        session_id: "stream-session",
        session_token: "stream-capability",
        identity_status: "unverified",
        user_id: null,
        status: "active",
      }),
    );
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (!String(input).endsWith("/messages/stream"))
        return original(input, init);
      const encoder = new TextEncoder();
      let timer: ReturnType<typeof setTimeout>;
      return Promise.resolve(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                encoder.encode(
                  'event: text\ndata: {"text":"Đoạn đầu đã kiểm tra."}\n\n',
                ),
              );
              timer = setTimeout(() => {
                controller.enqueue(
                  encoder.encode(
                    'event: result\ndata: {"session_id":"stream-session","reply":"Đoạn đầu đã kiểm tra. Đoạn tiếp theo.","used_fact_ids":[],"guard_events":[]}\n\n',
                  ),
                );
                controller.close();
              }, 1800);
            },
            cancel() {
              clearTimeout(timer);
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    };
  });
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json: { items: [] },
    }),
  );
  await page.goto("/chat#conversation");
  const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
  await expect(composer).toBeEnabled();
  await composer.fill("Tư vấn giúp tôi");
  await composer.press("Enter");
  await expect(
    page.getByText("Đoạn đầu đã kiểm tra.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Đoạn đầu đã kiểm tra. Đoạn tiếp theo.", { exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByText("Đoạn đầu đã kiểm tra. Đoạn tiếp theo.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("log").getByText("Tư vấn giúp tôi", { exact: true }),
  ).toHaveCount(1);
  await expect(composer).toBeEnabled();
  await expect(page.locator("#dob")).toHaveCount(0);
  await page.getByRole("button", { name: "Hiện thông tin hỗ trợ" }).click();
  await page
    .getByText("Thông tin đã ghi nhớ từ các kênh", { exact: true })
    .click();
  await expect(page.locator(".phone-context")).toContainText("40.000.000");
  await composer.fill("Tiếp tục tư vấn thẻ tín dụng");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("log")).toContainText("100 triệu đồng");
  await page.screenshot({
    path: testInfo.outputPath("phone-context-desktop.png"),
    fullPage: true,
  });
});

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
      return fulfillApi(route, {
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
      return fulfillApi(route, { json: { items: state.turns } });
    if (path.endsWith("/messages/stream")) {
      expect(route.request().headers().authorization).toBe(
        "Bearer anonymous-capability",
      );
      state.messageIds.push(body.client_message_id);
      if (failFirstMessage && state.messageIds.length === 1)
        return fulfillApi(route, {
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
      return fulfillApi(route, {
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
    return fulfillApi(route, {
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
      return fulfillApi(route, {
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? { authenticated: true, csrf_token: "test-csrf" }
          : { detail: "Unauthorized" },
      });
    if (path === "/staff/login") {
      loggedIn = true;
      return fulfillApi(route, { json: { csrf_token: "test-csrf" } });
    }
    if (path === "/eval/runs")
      return fulfillApi(route, {
        json: {
          schema_version: 1,
          runs: [{ run_id: "run-test", status: "incomplete", split: "dev" }],
        },
      });
    if (path === "/eval/runs/run-test")
      return fulfillApi(route, {
        json: {
          schema_version: 1,
          run_id: "run-test",
          status: "incomplete",
          split: "dev",
          summary: { harness: { price_error_rate: 0 } },
          judge: { status: "skipped" },
        },
      });
    return fulfillApi(route, { status: 404, json: { detail: "Not found" } });
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
      return fulfillApi(route, { json: { csrf_token: "test-csrf" } });
    if (path === "/handoffs")
      return fulfillApi(route, { json: { items: handoffs } });
    if (path === "/callbacks")
      return fulfillApi(route, { json: { items: [] } });
    return fulfillApi(route, { status: 404, json: { detail: "Not found" } });
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
    fulfillApi(route, { status: 401, json: { detail: "Unauthorized" } }),
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
