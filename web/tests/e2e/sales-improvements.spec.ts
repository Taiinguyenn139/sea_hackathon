import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test(`sales improvement plan saves, restores and compares results at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const action = {
      id: "relevant_offer",
      metric: "missing_offer_rate",
      label: "Thiếu đề xuất phù hợp",
      title: "Đưa ra phương án khi đã đủ điều kiện",
      priority: "medium",
      reason: "Có lượt thiếu đề xuất phù hợp.",
      current: 0.5,
      target: 0,
      steps: [
        "Kiểm tra điều kiện phù hợp.",
        "Đề nghị bước tiếp theo tự nguyện.",
      ],
      suggested_script: "Anh/chị muốn xem chi tiết không?",
      script: "Anh/chị muốn xem chi tiết không?",
      status: "draft",
      notes: "",
      affected_cases: 1,
      evidence: [
        {
          scenario_id: "needs-offer",
          repetition: 0,
          session_number: 1,
          step: 0,
          input: "Tôi muốn xem phương án.",
          reply: "Em đã ghi nhận.",
          issues: ["missing_offer"],
        },
      ],
    };
    let saved = { ...action };
    const baseline = {
      schema_version: 1,
      run_id: "baseline",
      status: "completed",
      split: "dev",
      summary: { harness: { missing_offer_rate: 0.5, conversion_rate: 0.5 } },
    };
    const candidate = { ...baseline, run_id: "candidate" };
    const plan = () => ({
      run_id: "baseline",
      eligible: true,
      mode: "harness",
      message: "Đề xuất dựa trên KPI của Memory Harness.",
      actions: [saved],
    });
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname.replace("/api", "");
      if (path === "/staff/me")
        return route.fulfill({
          json: { authenticated: true, csrf_token: "test-csrf" },
        });
      if (path === "/eval/runs")
        return route.fulfill({
          json: { schema_version: 1, runs: [baseline, candidate] },
        });
      if (path === "/eval/runs/baseline")
        return route.fulfill({ json: baseline });
      if (path === "/eval/runs/baseline/improvements")
        return route.fulfill({ json: plan() });
      if (path === "/eval/runs/baseline/improvements/relevant_offer") {
        expect(route.request().method()).toBe("PATCH");
        expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf");
        saved = { ...saved, ...route.request().postDataJSON() };
        return route.fulfill({ json: plan() });
      }
      if (path === "/eval/runs/baseline/comparison") {
        expect(url.searchParams.get("candidate_id")).toBe("candidate");
        return route.fulfill({
          json: {
            comparable: true,
            reasons: [],
            outcome: "improved",
            failed_gates: [],
            metrics: [
              {
                key: "conversion_rate",
                label: "Tỷ lệ chuyển đổi mô phỏng",
                before: 0.5,
                after: 0.75,
                delta: 0.25,
                direction: "improved",
              },
            ],
          },
        });
      }
      return route.fulfill({ status: 404, json: { detail: "Not found" } });
    });
    await page.goto("/benchmark");
    await expect(
      page.getByRole("heading", { name: "Cải thiện khả năng bán hàng" }),
    ).toBeVisible();
    await page.getByText("Dẫn chứng hội thoại", { exact: false }).click();
    await expect(
      page.getByText("Tôi muốn xem phương án.", { exact: false }),
    ).toBeVisible();
    await page
      .getByLabel("Lời tư vấn đề xuất")
      .fill("Anh/chị muốn em hướng dẫn hồ sơ phù hợp không?");
    await page
      .getByLabel("Ghi chú thay đổi đã thực hiện")
      .fill("Đã sửa lời tư vấn; cần benchmark lại.");
    await page.getByLabel("Tiến độ").selectOption("ready_for_evaluation");
    await page
      .getByRole("button", { name: "Lưu kế hoạch", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("Đã lưu kế hoạch.");
    await page.reload();
    await expect(page.getByLabel("Lời tư vấn đề xuất")).toHaveValue(
      saved.script,
    );
    await expect(page.getByLabel("Tiến độ")).toHaveValue(
      "ready_for_evaluation",
    );
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("button", { name: "Xuất kế hoạch đã lưu" }).click();
    expect((await downloadEvent).suggestedFilename()).toBe(
      "baseline-sales-improvements.json",
    );
    await page
      .getByLabel("Lượt đánh giá sau thay đổi")
      .selectOption("candidate");
    await expect(
      page.getByText("Có KPI cải thiện, các chỉ số được đối chiếu không giảm"),
    ).toBeVisible();
    await expect(page.getByText("+25 điểm %")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`improvements-${width}.png`),
      fullPage: true,
    });
  });
}

test("failed plan loading can be retried and incompatible comparisons show reasons", async ({
  page,
}) => {
  let failLoad = true;
  const baseline = {
    schema_version: 1,
    run_id: "baseline",
    status: "completed",
    split: "dev",
    summary: {},
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    if (path === "/staff/me")
      return route.fulfill({
        json: { authenticated: true, csrf_token: "csrf" },
      });
    if (path === "/eval/runs")
      return route.fulfill({
        json: {
          schema_version: 1,
          runs: [baseline, { ...baseline, run_id: "other" }],
        },
      });
    if (path === "/eval/runs/baseline")
      return route.fulfill({ json: baseline });
    if (path.endsWith("/improvements")) {
      if (failLoad) {
        return route.fulfill({
          status: 503,
          json: { detail: "Tạm thời chưa lập được kế hoạch." },
        });
      }
      return route.fulfill({
        json: {
          eligible: true,
          run_id: "baseline",
          mode: "harness",
          message: "Chưa đủ mẫu để đề xuất.",
          actions: [],
        },
      });
    }
    if (path.endsWith("/comparison"))
      return route.fulfill({
        json: {
          comparable: false,
          reasons: ["Dữ liệu đánh giá khác nhau: scenario_hash."],
          outcome: "not_comparable",
          metrics: [],
        },
      });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/benchmark");
  await expect(page.getByRole("alert")).toContainText(
    "Tạm thời chưa lập được kế hoạch.",
  );
  failLoad = false;
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Chưa có đề xuất cần thực hiện" }),
  ).toBeVisible();
  await page.getByLabel("Lượt đánh giá sau thay đổi").selectOption("other");
  await expect(
    page.getByText("Chưa thể so sánh hai lượt đánh giá"),
  ).toBeVisible();
  await expect(
    page.getByText("Dữ liệu đánh giá khác nhau: scenario_hash."),
  ).toBeVisible();
});
