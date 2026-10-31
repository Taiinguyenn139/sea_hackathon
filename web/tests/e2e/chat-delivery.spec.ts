import { expect, test, type Page, type Route } from "@playwright/test";

const session = {
  session_id: "delivery-session", session_token: "delivery-capability",
  identity_status: "unverified", demo_profile: "hoa",
};
const reply = { session_id: session.session_id, reply: "Mình đã nhận câu hỏi của bạn." };
const composerFor = (page: Page) => page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
function gate() {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release };
}
async function mockChat(page: Page, messages: (route: Route) => Promise<void>, history?: (route: Route) => Promise<void>) {
  await page.addInitScript((saved) => sessionStorage.setItem("bankwise-chat-session", JSON.stringify(saved)), session);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    if (path === "/demo/profiles") return route.fulfill({ json: { profiles: [{ id: "hoa", name: "Nguyễn Thị Hoa" }] } });
    if (path.endsWith("/turns")) return history ? history(route) : route.fulfill({ json: { items: [] } });
    if (path.endsWith("/messages")) return messages(route);
    return route.fulfill({ status: 404, json: { detail: "Unexpected request" } });
  });
}

for (const width of [375, 1440]) {
  test(`Enter shows the question before a slow response at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const response = gate();
    let requests = 0;
    await mockChat(page, async (route) => {
      requests += 1;
      expect(route.request().postDataJSON().text).toBe("Điều kiện vay mua nhà?");
      await response.ready;
      await route.fulfill({ json: reply });
    });
    await page.goto("/chat#conversation");
    const composer = composerFor(page);
    await composer.fill("Điều kiện vay mua nhà?");
    await composer.press("Enter");
    await expect(page.locator(".from-user .message-bubble")).toHaveText("Điều kiện vay mua nhà?");
    await expect(page.locator(".from-user")).toContainText("Đang gửi");
    await expect(page.locator(".from-user")).not.toContainText("Đã gửi");
    await expect(composer).toHaveValue("");
    await expect(composer).toBeDisabled();
    await expect(page.getByText("Trợ lý đang xử lý yêu cầu…", { exact: true })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect.poll(() => requests).toBe(1);
    await page.screenshot({ path: testInfo.outputPath(`acb-sending-${width}.png`), fullPage: true, animations: "disabled" });
    // A response arriving on home must not navigate away or lose the question.
    await page.getByRole("link", { name: "Về màn hình chính" }).click();
    response.release();
    await expect(page.getByRole("button", { name: /Vay mua nhà/ })).toBeEnabled();
    await expect(page).toHaveURL(/#home$/);
    await page.getByRole("button", { name: "Tiếp tục trò chuyện", exact: true }).click();
    await expect(page.locator(".from-user")).toHaveCount(1);
    await expect(page.locator(".from-user")).toContainText("Đã gửi");
    await expect(page.getByRole("log")).toContainText(reply.reply);
    await expect(composer).toBeFocused();
    expect(requests).toBe(1);
  });
}

test("failed messages stay visible and retry reuses the ID without losing a new draft", async ({ page }, testInfo) => {
  const ids: string[] = [];
  await mockChat(page, async (route) => {
    ids.push(route.request().postDataJSON().client_message_id);
    if (ids.length === 1) return route.fulfill({ status: 503, json: { detail: "Dịch vụ tạm thời chưa sẵn sàng." } });
    return route.fulfill({ json: reply });
  });
  await page.goto("/chat#conversation");
  const composer = composerFor(page);
  await composer.fill("Tôi muốn hỏi về lãi suất");
  await composer.press("Enter");
  await expect(page.locator(".from-user")).toContainText("Chưa gửi được");
  await expect(page.locator(".from-user")).not.toContainText("Đã gửi");
  await expect(composer).toBeFocused();
  await composer.fill("Câu hỏi tiếp theo đang soạn");
  await page.screenshot({ path: testInfo.outputPath("acb-send-error.png"), fullPage: true });
  await page.getByRole("button", { name: "Gửi lại", exact: true }).click();
  await expect(page.locator(".from-user")).toHaveCount(1);
  await expect(page.locator(".from-user")).toContainText("Đã gửi");
  await expect(page.getByRole("log")).toContainText(reply.reply);
  await expect(composer).toHaveValue("Câu hỏi tiếp theo đang soạn");
  await expect(composer).toBeFocused();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
});

test("Shift+Enter and IME composition keep text; Enter submits once", async ({ page }) => {
  const texts: string[] = [];
  await mockChat(page, async (route) => {
    texts.push(route.request().postDataJSON().text);
    return route.fulfill({ json: reply });
  });
  await page.goto("/chat#conversation");
  const composer = composerFor(page);
  await composer.fill("Tôi muốn vay");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("100");
  await expect(composer).toHaveValue("Tôi muốn vay\n100");
  await composer.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  await expect(composer).toHaveValue("Tôi muốn vay\n100");
  expect(texts).toHaveLength(0);
  // Two events in one browser task also exercise the synchronous send lock.
  await composer.evaluate((element) => {
    for (let i = 0; i < 2; i++) element.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  await expect(page.getByRole("log")).toContainText(reply.reply);
  expect(texts).toEqual(["Tôi muốn vay\n100"]);
});

test("restoring a long mobile conversation keeps the composer visible and prevents history races", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const history = gate();
  let requests = 0;
  await mockChat(page, async (route) => {
    requests += 1;
    return route.fulfill({ json: reply });
  }, async (route) => {
    await history.ready;
    await route.fulfill({ json: { items: Array.from({ length: 20 }, (_, index) => ({
      id: `history-${index}`, text: `Câu hỏi trước ${index}`, reply: `Nội dung trả lời ${index}`,
    })) } });
  });
  await page.goto("/chat#conversation");
  const composer = composerFor(page);
  await expect(composer).toBeDisabled();
  await expect(page.getByText("Đang khôi phục hội thoại…", { exact: true })).toBeVisible();
  history.release();
  await expect(composer).toBeFocused();
  await expect(page.locator(".from-user")).toHaveCount(20);
  await expect(composer).toBeInViewport();
  await composer.fill("Một câu hỏi mới " + "1234567890".repeat(25));
  await composer.press("Enter");
  await expect(page.getByRole("log")).toContainText(reply.reply);
  await expect(page.locator(".from-user")).toHaveCount(21);
  await expect(composer).toBeInViewport();
  await expect(page.locator(".from-assistant").last()).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  const bounds = await page.locator(".chat-main").boundingBox();
  expect(bounds!.height).toBeLessThanOrEqual(812);
  expect(requests).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("acb-long-mobile-chat.png"), fullPage: true, animations: "disabled" });
});
