import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const temp = await mkdtemp(join(tmpdir(), "bankwise-phone-demo-"));
const demoPhone = process.env.DEMO_PHONE_IDENTITY === "true";
const port = process.env.LIVE_TEST_PORT ?? "8008";
const base = `http://127.0.0.1:${port}`;
const server = spawn(
  resolve(root, ".venv/bin/python"),
  ["-m", "uvicorn", "harness.api:app", "--host", "127.0.0.1", "--port", port],
  {
    cwd: root,
    env: {
      ...process.env,
      APP_ENV: "development",
      HARNESS_DB: join(temp, "qa.sqlite"),
      LLM_BACKEND: "rules",
      TELEGRAM_BOT_TOKEN: "",
      TELEGRAM_WEBHOOK_SECRET: "local-test",
      HARNESS_RELEASE_GATE: "",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let logs = "";
server.stdout.on("data", (chunk) => {
  logs += chunk;
});
server.stderr.on("data", (chunk) => {
  logs += chunk;
});
let browser;
let number = 1;
async function tg(target, text, contact) {
  const id = number++;
  const r = await fetch(base + "/webhooks/telegram", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Bot-Api-Secret-Token": "local-test",
    },
    body: JSON.stringify({
      update_id: id,
      message: {
        date: 100 + id,
        chat: { id: target, type: "private" },
        from: { id: target, first_name: "QA" },
        ...(text ? { text } : {}),
        ...(contact
          ? { contact: { phone_number: contact, user_id: target } }
          : {}),
      },
    }),
  });
  expect(r.status).toBe(200);
  // The HTTP acknowledgement precedes background processing; wait for the durable binding via login below.
}
async function login(page, phone) {
  await page.goto(base + "/chat");
  if (demoPhone) {
    await page.getByLabel("Số điện thoại của bạn", { exact: true }).fill(phone);
    const response = page.waitForResponse((r) => r.url().endsWith("/demo/phone/session"));
    await page.getByRole("button", { name: "Bắt đầu chat", exact: true }).click();
    const session = await (await response).json();
    await expect(page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" })).toBeEnabled();
    return session;
  }
  await page
    .getByRole("button", { name: "Tiếp tục bằng số điện thoại" })
    .click();
  await page.getByLabel("Số điện thoại", { exact: true }).fill(phone);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/auth/phone/login"),
  );
  await page.getByRole("button", { name: "Bắt đầu trò chuyện" }).click();
  const session = await (await response).json();
  await expect(
    page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }),
  ).toBeEnabled();
  await expect(page.getByLabel("Mã xác nhận", { exact: true })).toHaveCount(0);
  return session;
}
async function send(page, text) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/messages/stream") && r.request().method() === "POST",
  );
  await page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }).fill(text);
  await page.getByRole("button", { name: "Gửi tin nhắn" }).click();
  const streamed = await response;
  expect(streamed.status()).toBe(200);
  await expect(page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" })).toBeEnabled();
}
async function waitIncome(phone, value) {
  await expect
    .poll(
      async () => {
        const s = await (
          await fetch(base + "/auth/phone/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone }),
          })
        ).json();
        return s.brief.needs.find((f) => f.key === "income")?.value;
      },
      { timeout: 10000 },
    )
    .toBe(value);
}
try {
  await expect
    .poll(
      async () => {
        try {
          return (await fetch(base + "/healthz")).status;
        } catch {
          return 0;
        }
      },
      { timeout: 15000 },
    )
    .toBe(200);
  browser = await chromium.launch({
    channel: process.env.CI ? undefined : "chrome",
  });
  // Telegram first -> UI reads memory -> UI update -> Telegram updates same customer.
  await tg(111, "/start");
  await tg(111, null, "0901234567");
  await tg(111, "Thu nhập chính xác 47 triệu");
  await waitIncome("0901234567", "47000000");
  const first = await browser.newPage({
    viewport: { width: 375, height: 812 },
  });
  const s1 = await login(first, "+84901234567");
  expect(s1.brief.needs.find((f) => f.key === "income").value).toBe("47000000");
  await send(first, "Thu nhập chính xác 53 triệu");
  await waitIncome("0901234567", "53000000");
  await tg(111, "Thu nhập chính xác 55 triệu");
  await waitIncome("0901234567", "55000000");
  // Web first -> shared contact attaches Telegram to the already existing customer.
  const second = await browser.newPage();
  const s2 = await login(second, "0901234568");
  await send(second, "Thu nhập chính xác 61 triệu");
  await tg(222, "/start");
  await tg(222, null, "+84901234568");
  await tg(222, "Thu nhập chính xác 63 triệu");
  await waitIncome("0901234568", "63000000");
  const third = await browser.newPage();
  const resumed = await login(third, "+84 90 123 4568");
  expect(resumed.brief.customer_id).toBe(s2.brief.customer_id);
  expect(resumed.brief.needs.find((f) => f.key === "income").value).toBe(
    "63000000",
  );
  await third.reload();
  await expect(
    third.getByRole("textbox", { name: "Tin nhắn cho trợ lý" }),
  ).toBeEnabled();
  console.log(
    "PASS real local backend + browser: phone-only login, no OTP, Telegram-first, web-first, shared memory updates, normalized numbers and reload. Telegram transport simulated through local webhook.",
  );
} catch (e) {
  console.error(e, logs.slice(-3000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise((r) => server.once("exit", r));
  await rm(temp, { recursive: true, force: true });
}
