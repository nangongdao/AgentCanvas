import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

test("online sources expose scheduling and lifecycle controls", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const url = `https://docs.example.test/handbook-${suffix}`;
  let knowledgeBaseId = "";
  let sourceId = "";
  let deleted = false;

  try {
    const knowledgeResponse = await request.post(`${API_URL}/api/knowledge-bases`, {
      headers: adminHeaders,
      data: { name: `Online Sources E2E ${suffix}` },
    });
    expect(knowledgeResponse.status()).toBe(201);
    knowledgeBaseId = ((await knowledgeResponse.json()) as { id: string }).id;

    await page.goto(`/knowledge?kb_id=${knowledgeBaseId}`);
    await login(page, "admin");
    const panel = page.locator("section").filter({
      has: page.getByRole("heading", { name: "Online sources", exact: true }),
    });
    await expect(panel.getByText("暂无在线源", { exact: true })).toBeVisible();

    await panel.getByRole("button", { name: "添加在线源", exact: true }).click();
    const sourceForm = page.getByTestId("online-source-form");
    await sourceForm.locator('input[type="url"]').fill(url);
    await sourceForm.locator('input[type="number"]').fill("3");
    await sourceForm.locator("select").nth(0).selectOption("1");
    await sourceForm.locator("select").nth(1).selectOption("60");
    const createResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}/online-sources`),
    );
    await panel.getByTitle("保存").click();
    const createdResponse = await createResponse;
    expect(createdResponse.status()).toBe(201);
    const source = (await createdResponse.json()) as {
      id: string;
      kb_id: string;
      url: string;
      max_pages: number;
      depth: number;
      status: string;
      sync_interval_minutes: number | null;
      next_sync_at: string | null;
      document_id: string | null;
    };
    sourceId = source.id;
    const sourceRow = panel.locator("article").filter({ hasText: url });
    await expect(sourceRow).toContainText("每小时");
    await expect(sourceRow).toContainText("pending");

    await sourceRow.getByRole("button", { name: `编辑 ${url}` }).click();
    await sourceForm.locator('input[type="number"]').fill("5");
    await sourceForm.locator("select").nth(1).selectOption("1440");
    const updateResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(
          `/api/knowledge-bases/${knowledgeBaseId}/online-sources/${sourceId}`,
        ),
    );
    await panel.getByTitle("保存").click();
    expect((await updateResponse).status()).toBe(200);
    await expect(panel.locator("article").filter({ hasText: url })).toContainText("每天");

    const syncedAt = new Date().toISOString();
    await page.route(
      `**/api/knowledge-bases/${knowledgeBaseId}/online-sources/${sourceId}/sync`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...source,
            max_pages: 5,
            status: "ready",
            sync_interval_minutes: 1440,
            last_synced_at: syncedAt,
            next_sync_at: new Date(Date.now() + 86_400_000).toISOString(),
            document_id: "f".repeat(32),
          }),
        });
      },
      { times: 1 },
    );
    await panel.getByRole("button", { name: `立即同步 ${url}` }).click();
    await expect(panel.locator("article").filter({ hasText: url })).toContainText("ready");
    await expect(page.getByText("在线源同步完成", { exact: true })).toBeVisible();

    await page.route(
      `**/api/knowledge-bases/${knowledgeBaseId}/online-sources`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              ...source,
              max_pages: 5,
              status: "failed",
              error: "scheduled refresh visible",
              sync_interval_minutes: 1440,
              next_sync_at: new Date(Date.now() + 86_400_000).toISOString(),
            },
          ]),
        });
      },
      { times: 1 },
    );
    await expect(panel.getByText("scheduled refresh visible", { exact: true })).toBeVisible({
      timeout: 10_000,
    });

    const accessibility = await new AxeBuilder({ page }).include("main").analyze();
    expect(
      accessibility.violations.filter(
        (violation) => violation.impact === "critical" || violation.impact === "serious",
      ),
    ).toEqual([]);

    await page.screenshot({
      path: testInfo.outputPath("online-sources-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({
      path: testInfo.outputPath("online-sources-mobile.png"),
      fullPage: true,
    });

    page.once("dialog", (confirmation) => void confirmation.accept());
    const deleteResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().endsWith(
          `/api/knowledge-bases/${knowledgeBaseId}/online-sources/${sourceId}`,
        ),
    );
    await panel.getByRole("button", { name: `删除 ${url}` }).click();
    expect((await deleteResponse).status()).toBe(204);
    await expect(panel.getByText("暂无在线源", { exact: true })).toBeVisible();
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    deleted = true;
  } finally {
    if (knowledgeBaseId) {
      const response = await request.delete(
        `${API_URL}/api/knowledge-bases/${knowledgeBaseId}`,
        { headers: adminHeaders },
      );
      if (deleted) expect(response.status()).toBe(204);
    }
  }
});
