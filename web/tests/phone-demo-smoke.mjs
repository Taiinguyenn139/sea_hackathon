// Offline integration of legacy phone-login APIs and the Telegram adapter.
// It does not exercise the main chat UI or prove LLM tool selection.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const temp = await mkdtemp(join(tmpdir(), "bankwise-phone-api-"));
const demoPhone = process.env.DEMO_PHONE_IDENTITY === "true";
const port = Number(process.env.LIVE_TEST_PORT ?? "8008");
assert(
  Number.isInteger(port) && port > 1023 && port < 65536 && port !== 8000,
  "Use a disposable port other than 8000.",
);
const base = `http://127.0.0.1:${port}`;
const revision = `phone-api-smoke-${randomBytes(8).toString("hex")}`;
const webhookSecret = randomBytes(32).toString("hex");
const server = spawn(
  resolve(root, ".venv/bin/python"),
  [
    "-m",
    "uvicorn",
    "harness.api:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: root,
    env: {
      ...process.env,
      APP_ENV: "development",
      HARNESS_DB: join(temp, "qa.sqlite"),
      HARNESS_RESULTS_DIR: join(temp, "results"),
      ADMIN_TOKEN: randomBytes(32).toString("hex"),
      STAFF_PASSWORD: randomBytes(32).toString("hex"),
      COOKIE_SECURE: "false",
      LLM_BACKEND: "rules",
      OPENAI_API_KEY: "",
      OPENAI_MODEL: "",
      OPENAI_BACKUP_MODEL: "",
      IDENTITY_HISTORY_ENABLED: "false",
      DEMO_PHONE_IDENTITY: String(demoPhone),
      TELEGRAM_BOT_TOKEN: "",
      TELEGRAM_WEBHOOK_SECRET: webhookSecret,
      HARNESS_RELEASE_GATE: "",
      DEPLOY_REVISION: revision,
    },
    stdio: ["ignore", "ignore", "ignore"],
  },
);
let startError = false;
server.on("error", () => {
  startError = true;
});
let number = 1;
async function api(path, body, token, extraHeaders = {}) {
  const response = await fetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extraHeaders,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(5000),
  });
  assert.equal(response.status, 200, "API request must succeed.");
  return response.json();
}
async function poll(check, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((done) => setTimeout(done, 200));
  }
  throw new Error("Expected state was not reached within the test budget.");
}
async function tg(target, text, contact) {
  const id = number++;
  await api(
    "/webhooks/telegram",
    {
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
    },
    undefined,
    { "X-Telegram-Bot-Api-Secret-Token": webhookSecret },
  );
}
async function login(phone, endpoint = "/auth/phone/login") {
  const session = await api(endpoint, { phone });
  assert(
    session.session_id && session.session_token,
    "Legacy login returns a session capability.",
  );
  assert.equal(
    session.identity_status,
    "identified",
    "This is a demo conversation identity.",
  );
  assert.equal(
    session.challenge_id,
    undefined,
    "Legacy phone-only login has no OTP challenge.",
  );
  assert.match(session.telegram_url, /^https:\/\/t\.me\//);
  return session;
}
async function send(session, text) {
  const reply = await api(
    `/sessions/${session.session_id}/messages`,
    { text, client_message_id: randomBytes(16).toString("hex") },
    session.session_token,
  );
  assert(reply.reply, "Rules backend returns a reply.");
}
async function waitIncome(phone, value) {
  await poll(async () => {
    const session = await login(phone);
    return (
      session.brief.needs.find((fact) => fact.key === "income")?.value === value
    );
  });
}
try {
  await poll(async () => {
    assert(
      !startError && server.exitCode === null,
      "Isolated backend exited during startup.",
    );
    try {
      const result = await api("/healthz");
      return result.revision === revision && result.backend === "rules";
    } catch {
      return false;
    }
  }, 15000);
  // The first order starts with Telegram, then continues through the legacy API.
  await tg(111, "/start");
  await tg(111, null, "0901234567");
  await tg(111, "Thu nhập chính xác 47 triệu");
  await waitIncome("0901234567", "47000000");
  const first = await login("+84901234567");
  assert.equal(
    first.brief.needs.find((fact) => fact.key === "income")?.value,
    "47000000",
  );
  await send(first, "Thu nhập chính xác 53 triệu");
  await waitIncome("0901234567", "53000000");
  await tg(111, "Thu nhập chính xác 55 triệu");
  await waitIncome("0901234567", "55000000");
  // The opposite order keeps web memory when the matching contact arrives.
  const second = await login("0901234568");
  await send(second, "Thu nhập chính xác 61 triệu");
  await waitIncome("0901234568", "61000000");
  await tg(222, "/start");
  await tg(222, null, "+84901234568");
  await tg(222, "Thu nhập chính xác 63 triệu");
  await waitIncome("0901234568", "63000000");
  const resumed = await login("+84 90 123 4568");
  assert.equal(resumed.brief.customer_id, second.brief.customer_id);
  assert.equal(
    resumed.brief.needs.find((fact) => fact.key === "income")?.value,
    "63000000",
  );
  const transcript = await api(
    `/sessions/${resumed.session_id}/turns`,
    undefined,
    resumed.session_token,
  );
  assert.deepEqual(
    transcript.items,
    [],
    "New sessions share memory without copying the old transcript.",
  );
  if (demoPhone) {
    const legacy = await login("+84901234568", "/demo/phone/session");
    assert.equal(legacy.brief.customer_id, resumed.brief.customer_id);
  }
  console.log(
    "PASS offline legacy API integration: phone-only sessions, normalized numbers, Telegram-first/API-first shared memory, separate transcripts. Rules backend and simulated local webhook; no browser, real Telegram transport or LLM tool selection tested.",
  );
} catch (error) {
  console.error(
    `FAIL legacy phone API smoke (${error instanceof Error ? error.name : "unknown error"}); values and backend logs omitted.`,
  );
  process.exitCode = 1;
} finally {
  if (!startError && server.exitCode === null) {
    const exited = new Promise((done) => server.once("exit", () => done(true)));
    server.kill("SIGTERM");
    const stopped = await Promise.race([
      exited,
      new Promise((done) => setTimeout(() => done(false), 15000).unref()),
    ]);
    if (!stopped) {
      console.error("Backend is still draining; temporary data was preserved.");
      process.exitCode = 1;
      server.unref();
    }
  }
  if (startError || server.exitCode !== null || server.signalCode !== null)
    await rm(temp, { recursive: true, force: true });
}
