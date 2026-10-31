import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const temp = await mkdtemp(join(tmpdir(), "bankwise-ui-integration-"));
const password = randomBytes(24).toString("hex");
const port = process.env.LIVE_TEST_PORT ?? "8007";
const base = `http://127.0.0.1:${port}`;
const output = resolve(root, "web/test-results/live");
await mkdir(output, { recursive: true });
let logs = "";
const server = spawn(
  resolve(root, ".venv/bin/python"),
  ["-m", "uvicorn", "harness.api:app", "--host", "127.0.0.1", "--port", port],
  {
    cwd: root,
    env: {
      ...process.env,
      HARNESS_DB: join(temp, "test.sqlite"),
      STAFF_PASSWORD: password,
      ADMIN_TOKEN: randomBytes(24).toString("hex"),
      LLM_BACKEND: "rules",
      COOKIE_SECURE: "false",
      HARNESS_RESULTS_DIR: join(temp, "results"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
server.stdout.on("data", (chunk) => {
  logs += chunk;
});
server.stderr.on("data", (chunk) => {
  logs += chunk;
});
let browser;
try {
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`${base}/healthz`);
      if (res.ok) {
        ready = true;
        break;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!ready) throw new Error(`Backend did not start: ${logs.slice(-2000)}`);
  browser = await chromium.launch({
    channel: process.env.CI ? undefined : "chrome",
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${base}/chat`);
  await page.getByRole("button", { name: "Bắt đầu", exact: true }).click();
  await page.locator("#dob").fill("1988-05-12");
  await page.getByRole("button", { name: "Xác minh", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Tin nhắn cho trợ lý" })
    .fill(
      "Tôi muốn vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, nợ 0, giá nhà 3 tỷ.",
    );
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await page.locator(".from-assistant").first().waitFor();
  await page.screenshot({
    path: join(output, "chat-verified.png"),
    fullPage: true,
  });
  await page.reload();
  await page.locator(".from-assistant").first().waitFor();
  await page
    .getByRole("textbox", { name: "Tin nhắn cho trợ lý" })
    .fill("Hẹn gọi lại cho tôi vào ngày mai lúc 09:00.");
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".from-assistant").length === 2,
  );
  await page
    .getByRole("button", { name: "Gặp chuyên viên", exact: true })
    .click();
  await page
    .getByText("Đang chờ chuyên viên tiếp nhận", { exact: true })
    .waitFor();
  await page.goto(`${base}/staff`);
  await page.getByLabel("Mật khẩu chuyên viên").fill(password);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await page.locator(".handoff-item").first().click();
  await page.getByRole("button", { name: "Xem nguồn", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  await page.screenshot({
    path: join(output, "staff-trace.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Đóng", exact: true }).click();
  await page
    .getByLabel("Ghi chú trao đổi")
    .fill("Đã trao đổi và xác nhận nhu cầu, tiếp tục chuẩn bị hồ sơ.");
  await page
    .getByRole("button", { name: "Hoàn tất bàn giao", exact: true })
    .click();
  await page
    .getByText("Đã hoàn tất và lưu nội dung bàn giao.", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: /Lịch gọi lại/ }).click();
  await page
    .locator(".callback-row")
    .first()
    .getByRole("button", { name: "Hoàn tất", exact: true })
    .click();
  await page
    .getByText("Đã đánh dấu cuộc hẹn hoàn tất.", { exact: true })
    .waitFor();
  await page.goto(`${base}/benchmark`);
  await page
    .getByRole("heading", { name: "Chưa có kết quả đánh giá", exact: true })
    .waitFor();
  await page.screenshot({
    path: join(output, "benchmark-empty.png"),
    fullPage: true,
  });
  await page.goto(`${base}/chat`);
  await page.waitForFunction(
    () =>
      !document.querySelector('textarea[aria-label="Tin nhắn cho trợ lý"]')
        ?.disabled,
    { timeout: 15000 },
  );
  if (errors.length) throw new Error(`Runtime errors: ${errors.join("; ")}`);
  console.log(
    "PASS live backend/browser: bootstrap, verify, sourced reply, reload transcript, handoff, staff login, trace dialog, completion, callback complete, benchmark empty, customer resume.",
  );
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise((r) => server.once("exit", r));
  await rm(temp, { recursive: true, force: true });
}
