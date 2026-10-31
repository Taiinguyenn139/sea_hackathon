import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
let server: ChildProcess;
let folder: string;
const port = 18769;
test.beforeAll(async () => {
  folder = mkdtempSync(join(tmpdir(), "sales-ui-"));
  const root = resolve("..");
  mkdirSync(join(folder, "results"));
  const run = JSON.parse(
    readFileSync(
      join(root, "eval/results/run-20261031-043822-6aaaff.json"),
      "utf8",
    ),
  );
  writeFileSync(
    join(folder, "results", `${run.run_id}.json`),
    JSON.stringify(run),
  );
  writeFileSync(
    join(folder, "results/index.json"),
    JSON.stringify({ schema_version: 1, runs: [run] }),
  );
  server = spawn(
    join(root, ".venv/bin/python"),
    ["-m", "uvicorn", "harness.api:app", "--port", String(port)],
    {
      cwd: root,
      stdio: "ignore",
      env: {
        ...process.env,
        HARNESS_DB: join(folder, "db.sqlite3"),
        HARNESS_RESULTS_DIR: join(folder, "results"),
        LLM_BACKEND: "rules",
        STAFF_PASSWORD: "sales-ui-test",
        ADMIN_TOKEN: "sales-ui-admin",
        APP_ENV: "development",
        HARNESS_RELEASE_GATE: "",
        TELEGRAM_BOT_TOKEN: "",
        TELEGRAM_WEBHOOK_SECRET: "",
        COOKIE_SECURE: "false",
      },
    },
  );
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("Isolated test server did not start");
});
test.afterAll(async () => {
  if (server) {
    server.kill();
    await new Promise<void>((r) => {
      if (server.exitCode !== null) r();
      else server.once("exit", () => r());
    });
  }
  if (folder) rmSync(folder, { recursive: true, force: true });
});
test("real API: publish, practise, grade, retry and preserve history on mobile", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const response = await route.fetch({
      url: `http://127.0.0.1:${port}${path}`,
    });
    await route.fulfill({ response });
  });
  await page.goto("/sales");
  await page.getByLabel("Mật khẩu chuyên viên").fill("sales-ui-test");
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Từ điều đã học, đến lần tư vấn tốt hơn.",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Luyện tập", exact: true }).click();
  await page.getByLabel("Mã chuyên viên", { exact: true }).fill("staff01");
  await page.getByLabel("Họ tên", { exact: true }).fill("Chuyên viên demo");
  await page.getByRole("button", { name: "Tạo hồ sơ", exact: true }).click();
  await expect(page.getByLabel("Hồ sơ đang luyện")).toContainText("staff01");
  await page.getByLabel("Tiêu đề", { exact: true }).fill("Xử lý băn khoăn");
  await page
    .getByLabel("Câu hỏi", { exact: true })
    .fill("Hãy tư vấn bước tiếp theo phù hợp.");
  await page.getByRole("button", { name: "Tạo bài nháp", exact: true }).click();
  await page.getByLabel("Người duyệt bài").fill("Lead");
  await page.getByLabel("Nhận xét rà soát").fill("Đã rà soát bối cảnh.");
  await page
    .getByRole("button", { name: "Phát hành bài luyện", exact: true })
    .click();
  await expect(page.getByLabel("Câu trả lời", { exact: true })).toBeVisible();
  await page
    .getByLabel("Hồ sơ đang luyện")
    .selectOption({ label: "staff01 · Chuyên viên demo" });
  await page
    .getByLabel("Câu trả lời", { exact: true })
    .fill(
      "Anh/chị muốn em giải thích thêm điều kiện trước khi trao đổi cùng chuyên viên không?",
    );
  await page
    .getByRole("button", { name: "Nộp bài / luyện lại", exact: true })
    .click();
  await expect(page.getByText("Lần 1: Chờ chấm")).toBeVisible();
  await page.getByRole("button", { name: "Chấm bài", exact: true }).click();
  const option = await page
    .getByRole("combobox", { name: /Lần nộp/ })
    .locator("option")
    .nth(1)
    .getAttribute("value");
  await page.getByRole("combobox", { name: /Lần nộp/ }).selectOption(option!);
  const rubric = [
    "Hiểu nhu cầu",
    "Chính xác và phù hợp",
    "Giải thích giá trị",
    "Xử lý băn khoăn",
    "Bước tiếp theo",
  ];
  for (const r of rubric) {
    await page.getByRole("combobox", { name: r }).selectOption("4");
    await page
      .getByLabel(`Nhận xét có dẫn chứng — ${r}`, { exact: true })
      .fill("Câu trả lời giải thích rõ và đề nghị tự nguyện.");
  }
  await page.getByLabel("Người chấm", { exact: true }).fill("Lead");
  await page
    .getByLabel("Nhận xét chung", { exact: true })
    .fill("Đã đối chiếu điều kiện.");
  await page.getByLabel("Đã đối chiếu số liệu", { exact: false }).check();
  await page
    .getByRole("button", { name: "Lưu điểm và phản hồi", exact: true })
    .click();
  await page.getByRole("button", { name: "Luyện tập", exact: true }).click();
  await expect(
    page.getByText("Điểm người chấm: 100/100", { exact: false }),
  ).toBeVisible();
  await page
    .getByLabel("Câu trả lời", { exact: true })
    .fill("Em sẵn sàng hỗ trợ giải thích thêm.");
  await page
    .getByRole("button", { name: "Nộp bài / luyện lại", exact: true })
    .click();
  await expect(page.getByText("Lần 2: Chờ chấm")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("practice-mobile.png"),
    fullPage: true,
  });
});
