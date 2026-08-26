import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  TOKENS,
  adminHeaders,
  captureUnexpectedErrors,
  logout,
} from "./support";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function holdNextRefresh(page: Page) {
  const started = deferred();
  const release = deferred();
  let intercepted = false;
  await page.route("**/api/auth/refresh", async (route) => {
    if (intercepted) {
      await route.continue();
      return;
    }
    intercepted = true;
    const response = await route.fetch();
    started.resolve();
    await release.promise;
    await route.fulfill({ response });
  });
  return { started: started.promise, release: release.resolve };
}

function requestStartsWithin(page: Page, path: string, timeoutMs = 300) {
  return Promise.race([
    page
      .waitForRequest((request) => request.url().endsWith(path))
      .then(() => true),
    new Promise<false>((resolve) => setTimeout(() => resolve(false), timeoutMs)),
  ]);
}

test("local account login uses the default password form and logs out", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const password = "BrowserPass!2026";

  // Register through the seeded admin token: this test exercises the login
  // form, not first-user bootstrap (covered by backend tests), and must not
  // depend on running before every other user-creating spec in the suite.
  const bootstrap = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email: `bootstrap-${suffix}@example.test`,
      password,
      display_name: "Browser Bootstrap",
      role: "admin",
    },
  });
  expect(bootstrap.status()).toBe(201);
  expect(((await bootstrap.json()) as { role: string }).role).toBe("admin");

  const email = `viewer-${suffix}@example.test`;
  const created = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email,
      password,
      display_name: "Browser Viewer",
      role: "viewer",
    },
  });
  expect(created.status()).toBe(201);
  expect(((await created.json()) as { role: string }).role).toBe("viewer");

  await page.context().clearCookies();
  await page.goto("/");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await expect(page.getByRole("button", { name: "账号", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");

  await logout(page);
  await expect(page.getByRole("button", { name: "账号", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("two tabs recover one expired access session without revoking refresh", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const email = `multitab-${suffix}@example.test`;
  const password = "BrowserPass!2026";
  const created = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email,
      password,
      display_name: "Multi Tab",
      role: "viewer",
    },
  });
  expect(created.status()).toBe(201);

  await page.goto("/");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");

  const secondPage = await page.context().newPage();
  await secondPage.goto("/");
  await expect(secondPage.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");

  await page.context().clearCookies({ name: "agentcanvas_session" });
  await Promise.all([page.reload(), secondPage.reload()]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");
  await expect(secondPage.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");

  const me = await page.request.get(`${API_URL}/api/auth/me`);
  expect(me.status()).toBe(200);
  await secondPage.close();
});

test("token login waits for an in-flight stale refresh", async ({ page }) => {
  await page.goto("/evaluations");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  const refresh = await holdNextRefresh(page);

  await page
    .getByRole("toolbar", { name: "评测页面操作" })
    .getByRole("button", { name: "刷新", exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  await refresh.started;

  await page.getByRole("button", { name: "API Token", exact: true }).click();
  await page.getByLabel("API Token").fill(TOKENS.admin);
  const loginStarted = requestStartsWithin(page, "/api/auth/login");
  const loaded = page.waitForEvent("load");
  await page
    .getByRole("button", { name: "登录", exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  const startedBeforeRelease = await loginStarted;
  refresh.release();

  expect(startedBeforeRelease).toBe(false);
  await loaded;
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("admin");
});

test("logout waits for an in-flight refresh and remains logged out", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const email = `logout-race-${suffix}@example.test`;
  const password = "BrowserPass!2026";
  const bootstrap = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email: `logout-bootstrap-${suffix}@example.test`,
      password,
      display_name: "Logout Bootstrap",
      role: "admin",
    },
  });
  expect(bootstrap.status()).toBe(201);
  const created = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email,
      password,
      display_name: "Logout Race",
      role: "viewer",
    },
  });
  expect(created.status()).toBe(201);

  await page.goto("/evaluations");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");

  const refreshButton = page
    .getByRole("toolbar", { name: "评测页面操作" })
    .getByRole("button", { name: "刷新", exact: true });
  await expect(refreshButton).toBeEnabled();
  await page.context().clearCookies({ name: "agentcanvas_session" });
  const refresh = await holdNextRefresh(page);
  await refreshButton.evaluate((button: HTMLButtonElement) => button.click());
  await refresh.started;

  await page.locator('button[title="当前访问角色与偏好"]').click();
  const logoutStarted = requestStartsWithin(page, "/api/auth/logout");
  const loaded = page.waitForEvent("load");
  await page
    .getByRole("button", { name: "退出登录", exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  const startedBeforeRelease = await logoutStarted;
  refresh.release();

  expect(startedBeforeRelease).toBe(false);
  await loaded;
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  const me = await page.request.get(`${API_URL}/api/auth/me`);
  expect(me.status()).toBe(401);
});
