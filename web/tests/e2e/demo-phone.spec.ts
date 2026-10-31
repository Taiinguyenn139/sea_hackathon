import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test(`demo flag keeps direct chat and anonymous new conversations at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let starts = 0;
    let legacyStarts = 0;
    let messages = 0;
    let siblings = 0;
    const transcripts = new Map<
      string,
      { id: string; text: string; reply: string }[]
    >();
    const sessions = new Map<
      string,
      {
        session_id: string;
        session_token: string;
        identity_status: "unverified" | "verified";
        status: string;
        user_id: string | null;
        brief: Record<string, unknown>;
      }
    >();
    const anonymous = (id: string) => {
      const session = {
        session_id: id,
        session_token: `token-${id}`,
        identity_status: "unverified" as const,
        status: "active",
        user_id: null,
        brief: { redacted: true },
      };
      sessions.set(id, session);
      transcripts.set(id, []);
      return session;
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/demo/profiles")
        return route.fulfill({
          json: {
            demo_phone_identity: true,
            profiles: [
              { id: "0900000002", phone: "0900000002", name: "Trần Minh An" },
            ],
          },
        });
      if (path === "/demo/phone/session") {
        legacyStarts += 1;
        return route.fulfill({
          status: 400,
          json: { detail: "Main chat must use anonymous sessions." },
        });
      }
      if (path === "/sessions") {
        expect(route.request().postDataJSON()).toEqual({ mode: "harness" });
        starts += 1;
        return route.fulfill({ json: anonymous("first") });
      }
      if (path.endsWith("/new")) {
        expect(route.request().headers().authorization).toBe(
          "Bearer token-first",
        );
        siblings += 1;
        return route.fulfill({
          json: anonymous(siblings === 1 ? "second" : "third"),
        });
      }
      if (path.endsWith("/messages/stream")) {
        const id = path.split("/")[2];
        const session = sessions.get(id)!;
        expect(route.request().headers().authorization).toBe(
          `Bearer token-${id}`,
        );
        const body = route.request().postDataJSON();
        messages += 1;
        let reply =
          "Tôi có thể giải thích thông tin công khai trước khi nhận diện hồ sơ.";
        if (body.text === "123") {
          expect(session.user_id).toBeNull();
          reply =
            "Số điện thoại không hợp lệ. Bạn vẫn có thể hỏi thông tin công khai.";
        } else if (body.text === "0900000002") {
          session.user_id = "0900000002";
          session.identity_status = "verified";
          session.brief = {
            customer_id: "minh",
            name: "Trần Minh An",
            needs: [],
          };
          reply =
            "Kết quả verify_identity đã kết nối hồ sơ Trần Minh An cho phiên này.";
        }
        if (body.text === "0900000001") {
          session.user_id = "0900000001";
          session.identity_status = "verified";
          session.brief = {
            customer_id: "hoa",
            name: "Nguyễn Thị Hoa",
            needs: [],
          };
          reply =
            "Kết quả verify_identity đã kết nối hồ sơ Nguyễn Thị Hoa cho phiên này.";
        }
        transcripts
          .get(id)!
          .push({ id: body.client_message_id, text: body.text, reply });
        return route.fulfill({
          contentType: "text/event-stream",
          body: `event: result\ndata: ${JSON.stringify({ ...session, reply, guard_events: [], used_fact_ids: [] })}\n\n`,
        });
      }
      if (path.endsWith("/turns"))
        return route.fulfill({
          json: { items: transcripts.get(path.split("/")[2]) ?? [] },
        });
      if (path.startsWith("/sessions/"))
        return route.fulfill({ json: sessions.get(path.split("/")[2]) });
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto("/chat");
    const composer = page.getByRole("textbox", { name: "Tin nhắn cho trợ lý" });
    await expect(composer).toBeEnabled();
    await expect(
      page.locator(
        'input[type="tel"], input[type="date"], #entry-phone, #demo-profile, #dob',
      ),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Bắt đầu chat", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", {
        name: "Tiếp tục bằng số điện thoại",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(starts).toBe(0);
    await composer.fill("123");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText(
      "Số điện thoại không hợp lệ",
    );
    await expect(composer).toBeEnabled();
    expect(starts).toBe(1);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            JSON.parse(
              sessionStorage.getItem("bankwise-chat-session") ?? "null",
            )?.user_id,
        ),
      )
      .toBeNull();
    await composer.fill("0900000002");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Trần Minh An");
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            JSON.parse(
              sessionStorage.getItem("bankwise-chat-session") ?? "null",
            )?.user_id,
        ),
      )
      .toBe("0900000002");
    expect(messages).toBe(2); // The phone is a message; its tool result updates this same session.
    await page.screenshot({
      path: testInfo.outputPath(`demo-direct-chat-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await composer.fill("Bản nháp của anh An");
    await page
      .getByRole("button", { name: "Bắt đầu phiên mới", exact: true })
      .click();
    await expect(page.getByRole("log")).toHaveCount(0);
    await expect(composer).toHaveValue("");
    await expect(composer).toBeEnabled();
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            JSON.parse(
              sessionStorage.getItem("bankwise-chat-session") ?? "null",
            )?.user_id,
        ),
      )
      .toBeNull();
    await composer.fill("Tư vấn vay mua nhà");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText(
      "thông tin công khai trước khi nhận diện",
    );
    await composer.fill("0900000001");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Nguyễn Thị Hoa");
    await expect(page.getByRole("log")).not.toContainText("Trần Minh An");
    expect(sessions.get("first")?.user_id).toBe("0900000002");
    expect(sessions.get("second")?.user_id).toBe("0900000001");
    await page.reload();
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("log")).toContainText("Nguyễn Thị Hoa");
    await expect(page.getByRole("log")).not.toContainText("Trần Minh An");
    const history =
      width === 375
        ? page.getByRole("dialog", { name: "Các cuộc trò chuyện" })
        : page.locator("#customer-history");
    if (width === 375)
      await page.getByRole("button", { name: "Mở danh sách phiên" }).click();
    await history.getByRole("button", { name: /123 Có thể tiếp tục/ }).click();
    await expect(composer).toHaveValue("Bản nháp của anh An");
    await expect(page.getByRole("log")).toContainText("Trần Minh An");
    await expect(page.getByRole("log")).not.toContainText("Nguyễn Thị Hoa");
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            JSON.parse(
              sessionStorage.getItem("bankwise-chat-session") ?? "null",
            )?.user_id,
        ),
      )
      .toBe("0900000002");
    // Reusing this person's phone requires a fresh claim in another anonymous session.
    await page
      .getByRole("button", { name: "Bắt đầu phiên mới", exact: true })
      .click();
    await expect(composer).toHaveValue("");
    await expect(page.getByRole("log")).toHaveCount(0);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            JSON.parse(
              sessionStorage.getItem("bankwise-chat-session") ?? "null",
            )?.user_id,
        ),
      )
      .toBeNull();
    await composer.fill("0900000002");
    await composer.press("Enter");
    await expect(page.getByRole("log")).toContainText("Trần Minh An");
    expect(transcripts.get("first")).toHaveLength(2);
    expect(transcripts.get("third")).toHaveLength(1);
    expect(starts).toBe(1);
    expect(siblings).toBe(2);
    expect(legacyStarts).toBe(0);
    expect(messages).toBe(5);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
  });
}
