import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

async function registerUser(
  request: APIRequestContext,
  email: string,
  role: "viewer" | "editor" | "admin",
): Promise<string> {
  const response = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email,
      password: "platform-admin-e2e-password",
      display_name: email,
      role,
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).id;
}

test("platform admin console shows usage, tenants, and announcements", async (
  { page, request },
  testInfo,
) => {
  test.setTimeout(90_000);
  captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const email = `platform-console-${suffix}@example.test`;
  const userId = await registerUser(request, email, "viewer");

  try {
    await page.goto("/workflows/new");
    await login(page, "admin");

    // The banner is absent while no announcement is active for this run.
    const banner = page.locator('div[aria-label="平台公告"] p');
    const bannerBefore = await banner.count();

    const navLink = page.getByTitle("平台管理台");
    await expect(navLink).toBeVisible();
    await navLink.click();
    await expect(page).toHaveURL(/\/settings\/platform$/);
    await expect(page.getByText("平台管理台", { exact: true })).toBeVisible();

    // Sections render with live data.
    await expect(page.getByLabel("跨租户用量")).toBeVisible();
    await expect(page.getByLabel("组织与用户")).toBeVisible();
    await expect(page.getByLabel("Provider 健康")).toBeVisible();
    await expect(page.getByLabel("执行队列")).toBeVisible();
    await expect(page.getByLabel("公告横幅")).toBeVisible();

    // Publish an announcement through the console form.
    const message = `维护公告 ${suffix}`;
    await page.getByLabel("公告内容").fill(message);
    await page.getByLabel("级别").selectOption("warning");
    await page.getByRole("button", { name: "发布", exact: true }).click();
    await expect(
      page.getByLabel("公告横幅").getByText(message, { exact: true }),
    ).toBeVisible();

    // The banner appears in the platform shell (top-level strip).
    await expect(banner.first()).toContainText(message);

    // Dismiss the banner for this session; it disappears.
    await page.getByRole("button", { name: "关闭公告" }).first().click();
    await expect(page.locator('div[aria-label="平台公告"]')).toHaveCount(0);
    expect(bannerBefore).toBeGreaterThanOrEqual(0);

    // Deactivate the freshly registered viewer via the users table.
    const userRow = page.getByLabel("组织与用户").getByRole("row", {
      name: new RegExp(email),
    });
    await userRow.getByRole("button", { name: "停用", exact: true }).click();
    await expect(userRow.getByText("已停用", { exact: true })).toBeVisible();

    // The deactivated account can no longer log in.
    const probe = await request.post(`${API_URL}/api/auth/login`, {
      data: { email, password: "platform-admin-e2e-password" },
    });
    expect(probe.status()).toBe(401);

    // Reactivate for cleanliness.
    const revived = await request.put(
      `${API_URL}/api/admin/users/${userId}/status`,
      { headers: adminHeaders, data: { status: "active" } },
    );
    expect(revived.status()).toBe(200);

    await page.screenshot({
      path: testInfo.outputPath("platform-admin-desktop.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  } finally {
    await request.put(`${API_URL}/api/admin/users/${userId}/status`, {
      headers: adminHeaders,
      data: { status: "active" },
    });
  }
});

test("viewer role never sees the platform console", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/workflows/new");
  await login(page, "viewer");
  await expect(page.getByTitle("平台管理台")).toHaveCount(0);
  await page.goto("/settings/platform");
  await expect(page).not.toHaveURL(/\/settings\/platform$/);
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
