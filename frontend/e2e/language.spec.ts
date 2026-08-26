import { expect, test } from "@playwright/test";

import {
  API_URL,
  FRONTEND_URL,
  adminHeaders,
  captureUnexpectedErrors,
} from "./support";

const PASSWORD = "LocalePass!2026";

test("language switches to english, persists, and lands in user settings", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const email = `locale-${suffix}@example.test`;

  // A real account row anchors the server-side preference (token subjects
  // keep a browser-local choice only).
  const created = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: { email, password: PASSWORD, display_name: "Locale Probe", role: "editor" },
  });
  expect(created.status()).toBe(201);

  try {
    await page.goto(`${FRONTEND_URL}/`);
    await page.locator("#auth-dialog-title").waitFor();
    // Default locale renders the Chinese login form.
    await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "账号", exact: true }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(PASSWORD);
    await Promise.all([
      page.waitForEvent("load"),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);

    // Shell renders in Chinese before any switch.
    const overviewLink = page.getByRole("link", { name: "概览", exact: true });
    await expect(overviewLink).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh");

    // Switch the language from the account menu.
    await page.locator('button[title="当前访问角色与偏好"]').click();
    const languageGroup = page.getByRole("radiogroup", { name: "语言" });
    await expect(languageGroup).toBeVisible();
    await languageGroup.getByRole("radio", { name: "English" }).click();

    // Nav labels, the document language, and the menu itself flip live.
    await expect(page.getByRole("link", { name: "Overview", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "概览", exact: true })).toHaveCount(0);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    // The command palette follows the locale.
    await page.getByRole("button", { name: "Search and commands" }).click();
    await expect(page.getByPlaceholder("Search workflows, apps, knowledge bases, and documents")).toBeVisible();
    await page.keyboard.press("Escape");

    // The preference lands in the account row (server-side) — read it back
    // through the page's own session cookies.
    const me = await page.request.get(`${API_URL}/api/auth/me`);
    expect(me.status()).toBe(200);
    expect(((await me.json()) as { language?: string | null }).language).toBe("en");

    // The preference persists across a full reload (localStorage round-trip).
    await page.reload();
    await expect(page.getByRole("link", { name: "Overview", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    // Switching back restores Chinese live.
    await page.locator('button[title="Current role and preferences"]').click();
    await page.getByRole("radiogroup", { name: "Language" }).getByRole("radio", { name: "中文" }).click();
    await expect(page.getByRole("link", { name: "概览", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh");

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
  } finally {
    // The account row itself stays (no user-delete route); like the auth
    // specs, this test uses a unique email per run.
  }
});
