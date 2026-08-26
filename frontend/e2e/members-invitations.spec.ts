import { expect, test, type Page } from "@playwright/test";

import { API_URL, adminHeaders, captureUnexpectedErrors } from "./support";

const PASSWORD = "members-e2e-password";

async function loginAccount(page: Page, email: string): Promise<void> {
  await page.addInitScript(() =>
    localStorage.setItem("agentcanvas:canvas-tour", "done"),
  );
  await page.goto("/");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(PASSWORD);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toBeVisible();
}

test("members page issues invitation link that a new user accepts", async (
  { page, request },
  testInfo,
) => {
  test.setTimeout(120_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const ownerEmail = `members-owner-${suffix}@example.test`;
  const inviteeEmail = `members-invitee-${suffix}@example.test`;

  // Owner account and organization are created over the API; the owner's
  // session cookie lives on the request context.
  const owner = await request.post(`${API_URL}/api/auth/register`, {
    data: {
      email: ownerEmail,
      password: PASSWORD,
      display_name: "Members Owner",
      role: "admin",
    },
  });
  expect(owner.status()).toBe(201);
  const org = await request.post(`${API_URL}/api/organizations`, {
    data: { name: `Members Org ${suffix}` },
  });
  expect(org.status()).toBe(201);
  const orgBody = (await org.json()) as { id: string; name: string };

  const invitee = await request.post(`${API_URL}/api/auth/register`, {
    headers: adminHeaders,
    data: {
      email: inviteeEmail,
      password: PASSWORD,
      display_name: "Members Invitee",
      role: "viewer",
    },
  });
  expect(invitee.status()).toBe(201);

  try {
    await loginAccount(page, ownerEmail);

    const membersLink = page.getByTitle("成员与邀请");
    await expect(membersLink).toBeVisible();
    await membersLink.click();
    await expect(page).toHaveURL(/\/settings\/members$/);

    await page.getByLabel("组织").selectOption({ label: orgBody.name });
    const roleSelects = page.getByLabel("成员列表").locator("select[id^='role-']");
    await expect(roleSelects.first()).toBeVisible();
    const before = await roleSelects.count();
    expect(before).toBeGreaterThanOrEqual(1);

    await page.getByLabel("被邀请人邮箱").fill(inviteeEmail);
    await page.getByLabel("邀请角色").selectOption("editor");
    await page.getByRole("button", { name: "生成邀请", exact: true }).click();

    const link = page.locator("p.break-all");
    await expect(link).toBeVisible();
    const acceptUrl = (await link.textContent()) ?? "";
    expect(acceptUrl).toContain("/invitations/accept?token=");
    const token = acceptUrl.split("token=")[1];
    await expect(
      page.getByLabel("邀请管理").getByText(inviteeEmail),
    ).toBeVisible();

    // The invitee accepts through a separate API session (cookie-scoped).
    const inviteeLogin = await request.post(`${API_URL}/api/auth/login`, {
      data: { email: inviteeEmail, password: PASSWORD },
    });
    expect(inviteeLogin.status()).toBe(200);
    const accepted = await request.post(
      `${API_URL}/api/organizations/invitations/accept`,
      { data: { token } },
    );
    expect(accepted.status()).toBe(200);

    // The reused invitation is burned: a second accept conflicts.
    const reused = await request.post(
      `${API_URL}/api/organizations/invitations/accept`,
      { data: { token } },
    );
    expect(reused.status()).toBe(409);

    // The owner's page reflects the new membership after a reload.
    await page.reload();
    await page.getByLabel("组织").selectOption({ label: orgBody.name });
    await expect.poll(() => roleSelects.count()).toBe(before + 1);

    // Static-token principals (no user session) are refused on accept.
    const rawProbe = await request.post(
      `${API_URL}/api/organizations/invitations/accept`,
      { headers: adminHeaders, data: { token: "z".repeat(43) } },
    );
    expect(rawProbe.status()).toBe(403);

    await page.screenshot({
      path: testInfo.outputPath("members-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
  } finally {
    await request.post(`${API_URL}/api/auth/login`, {
      data: { email: ownerEmail, password: PASSWORD },
    });
  }
});
