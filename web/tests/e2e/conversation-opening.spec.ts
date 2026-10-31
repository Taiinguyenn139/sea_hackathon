import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test.describe.configure({ mode: "serial" });
let backend: ChildProcess;
let origin: string;
let folder: string;

test.beforeAll(async () => {
  folder = await mkdtemp(join(tmpdir(), "bankwise-opening-"));
  const port = await new Promise<number>((done) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("No backend port");
      server.close(() => done(address.port));
    });
  });
  origin = `http://127.0.0.1:${port}`;
  backend = spawn("uv", ["run", "uvicorn", "harness.api:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: resolve(process.cwd(), ".."), stdio: "pipe",
    env: { ...process.env, HARNESS_DB: join(folder, "test.sqlite3"), APP_ENV: "development",
      LLM_BACKEND: "rules", AI_SPECIALIST_ENABLED: "false", DEMO_PHONE_IDENTITY: "true",
      TELEGRAM_BOT_TOKEN: "", TELEGRAM_WEBHOOK_SECRET: "", HARNESS_RELEASE_GATE: "" },
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
  if (folder) await rm(folder, { recursive: true, force: true });
});

for (const width of [375, 1440]) {
  test(`bot opens and recalls a returning customer's needs at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    let messageRequests = 0;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.includes("/messages")) messageRequests++;
      await route.fulfill({ response: await route.fetch({ url: origin + url.pathname.replace(/^\/api/, "") + url.search }) });
    });
    await page.goto("/chat");
    const phone = width === 375 ? "0901234567" : "0912345678";
    const enter = async () => {
      await page.getByLabel("Số điện thoại của bạn", { exact: true }).fill(phone);
      await page.getByRole("button", { name: "Bắt đầu chat", exact: true }).click();
    };
    await enter();
    const log = page.getByRole("log");
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(log).toContainText("Em là trợ lý Bankwise");
    await expect(log.locator(".from-assistant")).toHaveCount(1);
    await expect(log.locator(".from-user")).toHaveCount(0);
    await expect(page.getByLabel("Gợi ý tiếp tục hội thoại")).toHaveCount(0);
    await expect(composer).toBeEnabled();
    expect(messageRequests).toBe(0);
    await page.reload();
    await expect(log.locator(".from-assistant")).toHaveCount(1);
    await composer.fill("Tôi vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ");
    await composer.press("Enter");
    await expect(composer).toBeEnabled();
    await expect(log.locator(".from-user")).toHaveCount(1);
    await page.getByRole("button", { name: "Bắt đầu phiên mới", exact: true }).click();
    await enter();
    await expect(log).toContainText("Chào mừng anh/chị quay lại Bankwise");
    await expect(log).toContainText("2 tỷ đồng");
    await expect(log).toContainText("45 triệu đồng");
    await expect(log).toContainText("cập nhật");
    await expect(log.locator(".from-assistant")).toHaveCount(1);
    await expect(log.locator(".from-user")).toHaveCount(0);
    expect(messageRequests).toBe(1);
    await page.reload();
    await expect(log.locator(".from-assistant")).toHaveCount(1);
    await expect(log).toContainText("45 triệu đồng");
    await expect(composer).toBeEnabled();
    await page.screenshot({ path: info.outputPath(`returning-customer-${width}.png`), fullPage: true, animations: "disabled" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
