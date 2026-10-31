import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";

test.describe.configure({ mode: "serial" });
let backend: ChildProcess;
let origin: string;
test.beforeAll(async () => {
  const port = await new Promise<number>((resolvePort) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("No backend port");
      server.close(() => resolvePort(address.port));
    });
  });
  origin = `http://127.0.0.1:${port}`;
  backend = spawn("uv", ["run", "python", "web/tests/specialist_backend.py", String(port)], {
    cwd: resolve(process.cwd(), ".."), stdio: "pipe",
  });
  let errors = "";
  backend.stderr?.on("data", (data) => { errors += data.toString(); });
  await expect.poll(async () => {
    if (backend.exitCode !== null) throw new Error(errors);
    return fetch(`${origin}/health`).then((r) => r.status).catch(() => 0);
  }, { timeout: 15000 }).toBe(200);
});
test.afterAll(async () => {
  if (backend && backend.exitCode === null) {
    const stopped = new Promise<void>((done) => backend.once("exit", () => done()));
    backend.kill("SIGTERM");
    await stopped;
  }
});

for (const width of [375, 1440]) {
  test(`specialist receives context, survives reload and transfers to human at ${width}px`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const created = await request.post(`${origin}/sessions`, { data: { demo_profile: "hoa" } });
    const session = await created.json();
    const headers = { Authorization: `Bearer ${session.session_token}` };
    await request.post(`${origin}/sessions/${session.session_id}/verify`, { headers, data: { phone: "0900000001" } });
    await request.post(`${origin}/sessions/${session.session_id}/messages`, { headers, data: {
      text: "Tôi vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ", client_message_id: "needs",
    } });
    await page.addInitScript((saved) => {
      if (!sessionStorage.getItem("bankwise-chat-session"))
        sessionStorage.setItem("bankwise-chat-session", JSON.stringify(saved));
    }, { ...session, identity_status: "verified" });
    // Forward the app's API requests to a fresh SQLite-backed FastAPI, without mocking endpoints.
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: origin + url.pathname.replace(/^\/api/, "") + url.search });
      await route.fulfill({ response });
    });
    await page.goto("/chat#conversation");
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    await page.getByRole("button", { name: "Gặp chuyên viên", exact: true }).click();
    const log = page.getByRole("log");
    await expect(log).toContainText("Chuyên viên tư vấn");
    await expect(log).toContainText("Em đã tiếp nhận nội dung trao đổi về khoản vay 2 tỷ");
    await expect(composer).toBeEnabled();
    await composer.fill("Chuyên viên giải thích giúp tôi lãi sau ưu đãi");
    await composer.press("Enter");
    await expect(composer).toBeEnabled();
    await expect(log.locator(".from-assistant .message-label").last()).toHaveText("Chuyên viên tư vấn");
    await page.reload();
    await expect(log.locator(".from-assistant .message-label").first()).toHaveText("Trợ lý Bankwise");
    await expect(log.locator(".from-assistant .message-label").last()).toHaveText("Chuyên viên tư vấn");
    await expect(composer).toBeEnabled();
    await page.getByRole("button", { name: "Quay lại trợ lý", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`specialist-${width}.png`), fullPage: true, animations: "disabled" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.getByRole("button", { name: "Quay lại trợ lý", exact: true }).click();
    await expect(page.getByText("Đã lưu nội dung tư vấn. Bạn đang trao đổi với trợ lý.")).toBeVisible();
    await composer.fill("Cảm ơn");
    await composer.press("Enter");
    await expect(log.locator(".from-assistant .message-label").last()).toHaveText("Trợ lý Bankwise");
    await expect(composer).toBeEnabled();
    await page.getByRole("button", { name: "Gặp chuyên viên", exact: true }).click();
    await page.getByRole("button", { name: "Yêu cầu nhân viên thật", exact: true }).click();
    await expect(page.getByText("Đang chờ chuyên viên tiếp nhận", { exact: true })).toBeVisible();
    await expect(composer).toBeDisabled();
    const all = await request.get(`${origin}/handoffs`, { headers: { Authorization: "Bearer specialist-e2e-admin" } });
    const items = (await all.json()).items.filter((item: { session_id: string }) => item.session_id === session.session_id);
    expect(items.filter((item: { target: string; status: string }) => item.target === "human" && item.status === "pending")).toHaveLength(1);
    expect(items.filter((item: { target: string; status: string }) => item.target === "ai_specialist" && item.status === "completed")).toHaveLength(2);
  });
}

test("staff console distinguishes simulated consultations from the human queue", async ({ page, request }) => {
  const session = await (await request.post(`${origin}/sessions`, { data: { demo_profile: "hoa" } })).json();
  const headers = { Authorization: `Bearer ${session.session_token}` };
  await request.post(`${origin}/sessions/${session.session_id}/verify`, { headers, data: { phone: "0900000001" } });
  const started = await request.post(`${origin}/sessions/${session.session_id}/specialist/start`, {
    headers, data: { reason: "E2E console specialist", idempotency_key: "console" },
  });
  const opened = await started.json();
  expect(started.status()).toBe(200);
  const items = (await (await request.get(`${origin}/handoffs`, {
    headers: { Authorization: "Bearer specialist-e2e-admin" },
  })).json()).items;
  const pendingHumans = items.filter((item: { target: string; status: string }) => item.target === "human" && item.status !== "completed").length;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({ response: await route.fetch({ url: origin + url.pathname.replace(/^\/api/, "") + url.search }) });
  });
  await page.goto("/staff");
  await page.getByLabel("Mật khẩu chuyên viên").fill("specialist-e2e-staff");
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.locator(".work-stat").filter({ hasText: "Chờ tiếp nhận" }).locator("strong")).toHaveText(String(pendingHumans));
  await page.getByRole("button", { name: /E2E console specialist/ }).click();
  await expect(page.getByText("Chuyên viên mô phỏng · Đang xử lý", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Hoàn tất bàn giao", exact: true })).toHaveCount(0);
  await request.post(`${origin}/sessions/${session.session_id}/specialist/complete`, {
    headers, data: { handoff_id: opened.specialist_id, idempotency_key: "done" },
  });
  await page.getByRole("button", { name: "Làm mới danh sách", exact: true }).click();
  await page.getByRole("button", { name: /E2E console specialist/ }).click();
  await expect(page.getByText("Chuyên viên mô phỏng · Đã hoàn tất", { exact: true })).toBeVisible();
});
