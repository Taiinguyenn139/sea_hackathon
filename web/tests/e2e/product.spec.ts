import { expect, test } from "@playwright/test";

test("customer verifies before chat; retry reuses message id and displays real reply", async ({
  page,
}) => {
  let verified = false;
  const messageIds: string[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const body = route.request().postDataJSON();
    if (path === "/demo/profiles")
      return route.fulfill({
        json: {
          profiles: [{ id: "hoa", name: "Nguyễn Thị Hoa", dob: "1988-05-12" }],
        },
      });
    if (path === "/sessions")
      return route.fulfill({
        json: {
          session_id: "test-session",
          session_token: "test-capability",
          identity_status: "unverified",
          brief: { redacted: true },
        },
      });
    if (path.endsWith("/turns")) return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/verify")) {
      verified = body.dob === "1988-05-12";
      return route.fulfill({
        json: { identity_status: verified ? "verified" : "unverified" },
      });
    }
    if (path.endsWith("/messages")) {
      expect(verified).toBe(true);
      expect(route.request().headers().authorization).toBe(
        "Bearer test-capability",
      );
      messageIds.push(body.client_message_id);
      if (messageIds.length === 1)
        return route.fulfill({
          status: 503,
          json: { detail: "Dịch vụ tạm thời chưa sẵn sàng." },
        });
      return route.fulfill({
        json: {
          session_id: "test-session",
          reply: "Mình đã ghi nhận nhu cầu vay mua nhà của bạn.",
          used_fact_ids: [],
          guard_events: [],
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/chat");
  await expect(
    page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Bắt đầu", exact: true }).click();
  await expect(page.getByText("Xác minh trước khi tiếp tục")).toBeVisible();
  await page.locator("#dob").fill("1988-05-12");
  await page.getByRole("button", { name: "Xác minh", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Tin nhắn cho trợ lý" })
    .fill("Tôi muốn vay mua nhà");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await expect(page.getByRole("alert")).toContainText("Dịch vụ tạm thời");
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.getByRole("log")).toContainText("Mình đã ghi nhận");
  expect(messageIds[0]).toBe(messageIds[1]);
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
    await page.route("**/api/demo/profiles", (route) =>
      route.fulfill({
        json: {
          profiles: [{ id: "hoa", name: "Nguyễn Thị Hoa", dob: "1988-05-12" }],
        },
      }),
    );
    await page.goto("/chat");
    await expect(
      page.getByRole("heading", {
        name: "Mỗi kế hoạch lớn bắt đầu từ một cuộc trò chuyện.",
      }),
    ).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`chat-${width}.png`),
      fullPage: true,
    });
  });
