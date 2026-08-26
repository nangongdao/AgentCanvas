import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

test("admin audit workspace filters and traverses durable history", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const modelIds: string[] = [];
  const marker = `Audit browser ${suffix}`;
  const secret = `AUDIT-BROWSER-SECRET-${suffix}`;
  let bodyCompleted = false;

  try {
    for (let index = 0; index < 52; index += 1) {
      const id = `audit-browser-${suffix}-${index}`;
      const response = await request.post(`${API_URL}/api/models`, {
        headers: adminHeaders,
        data: {
          id,
          name: index === 0 ? marker : `Audit page ${suffix} ${index}`,
          provider: "mock",
          model_name: "mock",
          api_key: index === 0 ? secret : undefined,
        },
      });
      expect(response.status()).toBe(201);
      modelIds.push(id);
    }

    await page.goto("/workflows/new");
    await login(page, "admin");
    const auditLink = page.getByTitle("审计日志");
    await expect(auditLink).toBeVisible();
    await auditLink.click();
    await expect(page).toHaveURL(/\/settings\/audit$/);
    await expect(page.getByText("AgentCanvas Audit Log", { exact: true })).toBeVisible();

    const rows = page.locator('section[aria-label="审计事件"] article');
    await expect(rows).toHaveCount(50);
    await page.getByRole("button", { name: "加载更多", exact: true }).click();
    await expect.poll(() => rows.count()).toBeGreaterThan(50);

    await page.getByLabel("搜索审计日志").fill(marker);
    await page.getByLabel("动作筛选").selectOption("model.created");
    await page.getByLabel("资源类型筛选").selectOption("model");
    const filteredResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/api/audit-logs?") &&
        response.url().includes("action=model.created") &&
        response.url().includes("search=Audit"),
    );
    await page.getByRole("button", { name: "筛选", exact: true }).click();
    expect((await filteredResponse).status()).toBe(200);
    await expect(rows).toHaveCount(1);
    await expect(page.getByText(marker, { exact: true })).toBeVisible();
    await expect(rows.getByText("创建模型", { exact: true })).toBeVisible();
    await expect(rows.getByText("has_key", { exact: true })).toBeVisible();
    await expect(page.getByText(secret, { exact: true })).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("audit-desktop.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await expect(page.getByText(marker, { exact: true })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("audit-mobile.png"),
      fullPage: true,
    });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    for (const id of modelIds) {
      const removed = await request.delete(`${API_URL}/api/models/${id}`, {
        headers: adminHeaders,
      });
      if (bodyCompleted) expect(removed.status()).toBe(204);
    }
  }
});

test("viewer cannot discover or open the audit workspace", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/workflows/new");
  await login(page, "viewer");
  await expect(page.getByTitle("审计日志")).toHaveCount(0);

  await page.goto("/audit");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("AgentCanvas Audit Log", { exact: true })).toHaveCount(0);
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
