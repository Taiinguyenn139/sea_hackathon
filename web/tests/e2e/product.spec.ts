import { expect, test, type Route } from "@playwright/test";

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

const demoProfiles = Array.from({ length: 10 }, (_, index) => {
  const phone = `09000000${String(index + 1).padStart(2, "0")}`;
  return {
    id: phone,
    phone,
    name: index === 0 ? "Nguyễn Thị Hoa" : `Khách hàng demo ${index + 1}`,
    runtime_profile: index === 0 ? "hoa" : index === 1 ? "minh" : null,
  };
});

test("chat shows partial text while the streamed reply is still in progress", async ({
  page,
}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "bankwise-chat-session",
      JSON.stringify({
        session_id: "stream-session",
        session_token: "stream-capability",
        identity_status: "identified",
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
      json: route.request().url().includes("/demo/profiles")
        ? { profiles: demoProfiles }
        : { items: [] },
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
  await expect(page.getByRole("log").getByText("Tư vấn giúp tôi", { exact: true })).toHaveCount(
    1,
  );
  await expect(composer).toBeEnabled();
});

test("phone confirmation restores Telegram context before enabling web chat", async ({
  page,
}, testInfo) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const body = route.request().postDataJSON();
    if (path === "/demo/profiles")
      return fulfillApi(route, { json: { profiles: demoProfiles } });
    if (path === "/auth/phone/start") {
      expect(body.phone).toBe("0901234567");
      return fulfillApi(route, {
        json: { challenge_id: "test-phone-challenge", expires_in: 600 },
      });
    }
    if (path === "/auth/phone/confirm") {
      expect(body.challenge_id).toBe("test-phone-challenge");
      if (body.code !== "654321")
        return fulfillApi(route, {
          status: 400,
          json: { detail: "Mã xác nhận không hợp lệ hoặc đã hết hạn." },
        });
      return fulfillApi(route, {
        json: {
          session_id: "phone-session",
          session_token: "phone-capability",
          identity_status: "identified",
          brief: {
            customer_id: "customer-test",
            name: "Khách từ Telegram",
            phone: "+84901234567",
            needs: [
              {
                id: "income",
                key: "income",
                value: "40000000",
                unit: "đồng",
                source_type: "customer",
                channel: "telegram",
              },
            ],
          },
        },
      });
    }
    if (path.endsWith("/turns"))
      return fulfillApi(route, { json: { items: [] } });
    if (path.endsWith("/messages") || path.endsWith("/messages/stream")) {
      expect(route.request().headers().authorization).toBe(
        "Bearer phone-capability",
      );
      return fulfillApi(route, {
        json: {
          session_id: "phone-session",
          reply:
            "Dựa trên nhu cầu đã trao đổi qua Telegram, hạn mức thẻ tham khảo đến 100 triệu đồng.",
          used_fact_ids: [],
          guard_events: [],
        },
      });
    }
    return fulfillApi(route, { status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/chat");
  await page
    .getByRole("button", { name: "Tiếp tục bằng số điện thoại" })
    .click();
  await page.getByLabel("Số điện thoại", { exact: true }).fill("0901234567");
  await page.getByRole("button", { name: "Nhận mã qua Telegram" }).click();
  const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
  await expect(composer).toBeDisabled();
  await page.getByLabel("Mã xác nhận", { exact: true }).fill("111111");
  await page.getByRole("button", { name: "Xác nhận và tiếp tục" }).click();
  await expect(page.getByRole("alert")).toContainText("không hợp lệ");
  await expect(composer).toBeDisabled();
  await page.getByLabel("Mã xác nhận", { exact: true }).fill("654321");
  await page.getByRole("button", { name: "Xác nhận và tiếp tục" }).click();
  await expect(composer).toBeEnabled();
  await expect(page.locator("#dob")).toHaveCount(0);
  await page.getByRole("button", { name: "Hiện thông tin hỗ trợ" }).click();
  await page
    .getByText("Thông tin đã ghi nhận từ Telegram", { exact: true })
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

test("phone onboarding fits a narrow screen", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route("**/api/**", (route) =>
    fulfillApi(route, { json: { profiles: [] } }),
  );
  await page.goto("/chat");
  await page
    .getByRole("button", { name: "Tiếp tục bằng số điện thoại" })
    .click();
  await expect(page.getByLabel("Số điện thoại", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(375);
  await page.screenshot({
    path: testInfo.outputPath("phone-login-mobile.png"),
    fullPage: true,
  });
});

test("demo phone in chat verifies the session; public questions and retry work", async ({
  page,
}, testInfo) => {
  let verified = false;
  let publicQuestions = 0;
  const messageIds: string[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const body = route.request().postDataJSON();
    if (path === "/demo/profiles")
      return fulfillApi(route, { json: { profiles: demoProfiles } });
    if (path === "/sessions") {
      expect(body.demo_profile).toBe("0900000001");
      return fulfillApi(route, {
        json: {
          session_id: "test-session",
          session_token: "test-capability",
          user_id: "0900000001",
          identity_status: "unverified",
          brief: { redacted: true },
        },
      });
    }
    if (path.endsWith("/turns"))
      return fulfillApi(route, { json: { items: [] } });
    if (path.endsWith("/messages") || path.endsWith("/messages/stream")) {
      expect(route.request().headers().authorization).toBe(
        "Bearer test-capability",
      );
      if (body.text === "Tôi muốn tìm hiểu vay mua nhà.") {
        expect(verified).toBe(false);
        publicQuestions += 1;
        return fulfillApi(route, {
          json: {
            session_id: "test-session",
            identity_status: "unverified",
            user_id: "0900000001",
            reply:
              "Tôi có thể giải thích thông tin vay mua nhà công khai trước xác minh.",
            used_fact_ids: [],
            guard_events: [],
          },
        });
      }
      if (body.text === "0900000001") {
        verified = true;
        return fulfillApi(route, {
          json: {
            session_id: "test-session",
            identity_status: "verified",
            user_id: "0900000001",
            brief: { customer_id: "hoa", name: "Nguyễn Thị Hoa", needs: [] },
            reply: "Đã xác minh hồ sơ demo cho phiên này.",
            used_fact_ids: [],
            guard_events: [],
          },
        });
      }
      expect(verified).toBe(true);
      messageIds.push(body.client_message_id);
      if (messageIds.length === 1)
        return fulfillApi(route, {
          status: 503,
          json: { detail: "Dịch vụ tạm thời chưa sẵn sàng." },
        });
      return fulfillApi(route, {
        json: {
          session_id: "test-session",
          identity_status: "verified",
          user_id: "0900000001",
          reply: "Mình đã ghi nhận nhu cầu vay mua nhà của bạn.",
          used_fact_ids: [],
          guard_events: [],
        },
      });
    }
    return fulfillApi(route, { status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
  await expect(composer).toBeDisabled();
  await expect(page.locator("#demo-profile option")).toHaveCount(10);
  await page.getByRole("button", { name: /Vay mua nhà/ }).click();
  await expect(
    page.getByRole("button", { name: /Vay mua nhà/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(composer).toHaveValue("Tôi muốn tìm hiểu vay mua nhà.");
  await page.getByRole("button", { name: "Bắt đầu", exact: true }).click();
  await expect(
    page.getByText("Tiếp nối hồ sơ và đặt lịch", { exact: true }),
  ).toBeVisible();
  await expect(composer).toBeEnabled();
  await expect(page.locator('input[type="date"], #dob')).toHaveCount(0);
  await expect(page.getByText(/Ngày sinh/i)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Gặp chuyên viên", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("log")).toContainText("công khai trước xác minh");
  expect(publicQuestions).toBe(1);
  await expect(
    page.getByText("Tiếp nối hồ sơ và đặt lịch", { exact: true }),
  ).toBeVisible();
  await page.getByText("Tiếp nối hồ sơ và đặt lịch", { exact: true }).click();
  await expect(
    page.getByText("Số điện thoại demo đã chọn: 0900000001", { exact: true }),
  ).toBeVisible();
  await composer.fill("0900000001");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("log")).toContainText("Đã xác minh hồ sơ demo");
  await expect(
    page.getByText("Tiếp nối hồ sơ và đặt lịch", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Gặp chuyên viên", exact: true }),
  ).toBeEnabled();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("bankwise-chat-session") ?? "null")
            ?.identity_status,
      ),
    )
    .toBe("verified");
  await composer.fill("Tôi muốn vay mua nhà");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("alert")).toContainText("Dịch vụ tạm thời");
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.getByRole("log")).toContainText("Mình đã ghi nhận");
  expect(messageIds[0]).toBe(messageIds[1]);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: testInfo.outputPath("chat-conversation.png"),
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
    await page.route("**/api/demo/profiles", (route) =>
      fulfillApi(route, {
        json: {
          profiles: demoProfiles,
        },
      }),
    );
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
  await page.route("**/api/demo/profiles", (route) =>
    fulfillApi(route, { json: { profiles: [] } }),
  );
  await page.goto("/chat");
  await expect(page.locator(".illustrated-card")).toHaveCSS(
    "animation-name",
    "none",
  );
});
