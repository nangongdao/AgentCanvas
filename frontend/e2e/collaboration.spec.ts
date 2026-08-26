import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  FRONTEND_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
} from "./support";

const PASSWORD = "BrowserPass!2026";

async function loginAccount(page: Page, workflowId: string, email: string) {
  await page.goto(`/workflows/${workflowId}`);
  // Dismiss the first-visit canvas tour suite-wide (see support.login).
  await page.addInitScript(() =>
    localStorage.setItem("agentcanvas:canvas-tour", "done"),
  );
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(PASSWORD);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toBeVisible();
}

test("two editors transfer a soft lock and retain optimistic conflict recovery", async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const aliceEmail = `collab-alice-${suffix}@example.test`;
  const bobEmail = `collab-bob-${suffix}@example.test`;
  const originalName = `Collaboration E2E ${suffix}`;
  const savedByBob = `${originalName} Bob`;
  const staleAliceName = `${originalName} Alice stale`;
  const workflow = await createWorkflow(request, originalName);
  const aliceErrors = captureUnexpectedErrors(page);
  const bobContext = await browser.newContext({
    baseURL: FRONTEND_URL,
  });
  const bobPage = await bobContext.newPage();
  const bobErrors = captureUnexpectedErrors(bobPage);
  let bodyCompleted = false;

  try {
    for (const [email, displayName] of [
      [aliceEmail, "Collaboration Alice"],
      [bobEmail, "Collaboration Bob"],
    ]) {
      const created = await request.post(`${API_URL}/api/auth/register`, {
        headers: adminHeaders,
        data: {
          email,
          password: PASSWORD,
          display_name: displayName,
          role: "editor",
        },
      });
      expect(created.status()).toBe(201);
    }

    await loginAccount(page, workflow.id, aliceEmail);
    const aliceStatus = page.getByTestId("collaboration-status");
    const aliceName = page.locator('input[placeholder="工作流名称"]');
    await expect(aliceStatus).toContainText("我在编辑");
    await expect(aliceName).toBeEditable();

    await loginAccount(bobPage, workflow.id, bobEmail);
    const bobStatus = bobPage.getByTestId("collaboration-status");
    const bobName = bobPage.locator('input[placeholder="工作流名称"]');
    await expect(bobStatus).toContainText(aliceEmail);
    await expect(bobPage.getByTestId("collaboration-takeover")).toBeVisible();
    await expect(bobName).not.toBeEditable();
    await expect(page.getByTestId("collaboration-participant-count")).toHaveText("2");

    await bobPage.getByTestId("collaboration-takeover").click();
    await expect(bobStatus).toContainText("我在编辑");
    await expect(bobName).toBeEditable();
    await expect(aliceStatus).toContainText(bobEmail);
    await expect(aliceName).not.toBeEditable();
    await expect(
      page.getByRole("button", { name: "运行", exact: true }),
    ).toHaveCount(0);

    const bobSave = bobPage.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(`/api/workflows/${workflow.id}`),
    );
    await bobName.fill(savedByBob);
    expect((await bobSave).status()).toBe(200);
    await expect(bobPage.getByText("synced", { exact: true })).toBeVisible();

    const leave = bobPage.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().includes(`/api/workflows/${workflow.id}/collaboration/presence/`),
    );
    await bobPage.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent("pagehide"));
    });
    expect((await leave).status()).toBe(200);
    await bobPage.close();

    await expect(page.getByTestId("collaboration-participant-count")).toHaveText("1");
    await expect(page.getByTestId("collaboration-lock-owner")).toHaveText(
      "当前无人编辑",
    );
    await page.getByTestId("collaboration-takeover").click();
    await expect(aliceStatus).toContainText("我在编辑");
    await expect(aliceName).toBeEditable();

    const staleSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(`/api/workflows/${workflow.id}`),
    );
    await aliceName.fill(staleAliceName);
    expect((await staleSave).status()).toBe(409);
    const conflict = page.getByRole("alertdialog", { name: "工作流版本冲突" });
    await expect(conflict).toBeVisible();
    await conflict.getByRole("button", { name: "载入远端" }).click();
    await expect(aliceName).toHaveValue(savedByBob);

    expect(aliceErrors.pageErrors).toEqual([]);
    expect(aliceErrors.consoleErrors).toEqual([]);
    expect(
      aliceErrors.httpErrors.filter(
        (entry) =>
          !entry.startsWith("409 ") ||
          !entry.endsWith(`/api/workflows/${workflow.id}`),
      ),
    ).toEqual([]);
    expect(
      aliceErrors.httpErrors.filter(
        (entry) =>
          entry.startsWith("409 ") &&
          entry.endsWith(`/api/workflows/${workflow.id}`),
      ),
    ).toHaveLength(1);
    expect(bobErrors.pageErrors).toEqual([]);
    expect(bobErrors.consoleErrors).toEqual([]);
    const expectedLockConflicts = bobErrors.httpErrors.filter(
      (entry) =>
        entry.startsWith("409 ") &&
        entry.endsWith(`/api/workflows/${workflow.id}/collaboration/lock`),
    );
    expect(expectedLockConflicts.length).toBeGreaterThanOrEqual(1);
    expect(
      bobErrors.httpErrors.filter(
        (entry) => !expectedLockConflicts.includes(entry),
      ),
    ).toEqual([]);
    bodyCompleted = true;
  } finally {
    await bobContext.close();
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("one tab keeps its collaboration identity across the login reload", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const email = `collab-reload-${suffix}@example.test`;
  const workflow = await createWorkflow(request, `Collaboration reload ${suffix}`);
  const streamClientIds: string[] = [];
  let bodyCompleted = false;

  page.on("request", (outbound) => {
    if (
      outbound.method() !== "GET" ||
      !outbound
        .url()
        .includes(`/api/workflows/${workflow.id}/collaboration/stream?`)
    ) {
      return;
    }
    const clientId = new URL(outbound.url()).searchParams.get("client_id");
    if (clientId) streamClientIds.push(clientId);
  });

  try {
    const created = await request.post(`${API_URL}/api/auth/register`, {
      headers: adminHeaders,
      data: {
        email,
        password: PASSWORD,
        display_name: "Collaboration Reload",
        role: "editor",
      },
    });
    expect(created.status()).toBe(201);

    await loginAccount(page, workflow.id, email);
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");
    await expect.poll(() => streamClientIds.length).toBeGreaterThan(0);
    const beforeReload = streamClientIds.at(-1);
    const streamCount = streamClientIds.length;

    await page.reload();
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");
    await expect.poll(() => streamClientIds.length).toBeGreaterThan(streamCount);

    expect(new Set(streamClientIds)).toEqual(new Set([beforeReload]));
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
