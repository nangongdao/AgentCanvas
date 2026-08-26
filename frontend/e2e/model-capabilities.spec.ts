import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

test("model capability matrix narrows effective behavior on desktop and mobile", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const modelId = `capability-browser-${suffix}`;
  const modelName = `Capability Browser ${suffix}`;
  let created = false;
  try {
    const response = await request.post(`${API_URL}/api/models`, {
      headers: adminHeaders,
      data: {
        id: modelId,
        name: modelName,
        provider: "openai_compat",
        model_name: "browser-test",
        prompt_price_per_million_usd: "1",
        completion_price_per_million_usd: "2",
      },
    });
    expect(response.status()).toBe(201);
    created = true;

    await page.goto("/models");
    await login(page, "admin");
    await expect(
      page.getByRole("table", { name: "能力矩阵" }),
    ).toBeVisible();
    const runtimeHealth = page.getByRole("region", { name: "Provider 运行健康" });
    await expect(runtimeHealth).toBeVisible();
    await expect(runtimeHealth).toContainText(/observed/);
    const providerRow = page.getByRole("row").filter({ hasText: "openai_compat" });
    await expect(providerRow).toContainText("JSON");

    const modelRow = page.locator("article").filter({ hasText: modelName });
    await expect(modelRow).toBeVisible();
    await expect(
      modelRow.locator('[data-capability="tools"][data-enabled="true"]'),
    ).toBeVisible();
    await modelRow.getByRole("button", { name: "编辑" }).click();

    const dialog = page.getByRole("dialog", { name: "编辑模型" });
    const tools = dialog.getByLabel("工具", { exact: true });
    await expect(tools).toBeChecked();
    await tools.uncheck();
    const updateResponse = page.waitForResponse(
      (candidate) =>
        candidate.request().method() === "PUT" &&
        candidate.url().endsWith(`/api/models/${modelId}`),
    );
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    const updated = await updateResponse;
    expect(updated.status()).toBe(200);
    const payload = (await updated.json()) as {
      capability_overrides: Record<string, boolean>;
      capabilities: Record<string, boolean>;
    };
    expect(payload.capability_overrides).toEqual({ tools: false });
    expect(payload.capabilities.tools).toBe(false);
    await expect(dialog).toBeHidden();
    await expect(
      modelRow.locator('[data-capability="tools"][data-enabled="false"]'),
    ).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await modelRow.getByRole("button", { name: "编辑" }).click();
    const mobileDialog = page.getByRole("dialog", { name: "编辑模型" });
    await expect(mobileDialog.getByLabel("工具", { exact: true })).not.toBeChecked();
    const panel = mobileDialog.locator(":scope > div");
    const dimensions = await panel.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      width: element.getBoundingClientRect().width,
    }));
    expect(dimensions.clientHeight).toBeLessThanOrEqual(828);
    expect(dimensions.scrollHeight).toBeGreaterThanOrEqual(dimensions.clientHeight);
    expect(dimensions.width).toBeLessThanOrEqual(390);
    const pageLayout = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      overflowers: Array.from(document.querySelectorAll<HTMLElement>("body *"))
        .map((element) => {
          const bounds = element.getBoundingClientRect();
          return {
            className: element.className.toString().slice(0, 120),
            clientWidth: element.clientWidth,
            right: Math.round(bounds.right),
            scrollWidth: element.scrollWidth,
            tag: element.tagName,
            width: Math.round(bounds.width),
          };
        })
        .filter(
          (element) =>
            element.right > window.innerWidth ||
            element.scrollWidth > element.clientWidth,
        )
        .slice(0, 20),
    }));
    expect(
      pageLayout.documentWidth,
      JSON.stringify(pageLayout.overflowers, null, 2),
    ).toBeLessThanOrEqual(pageLayout.viewportWidth);
    await mobileDialog.getByRole("button", { name: "关闭" }).click();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
  } finally {
    if (created) {
      const removed = await request.delete(`${API_URL}/api/models/${modelId}`, {
        headers: adminHeaders,
      });
      expect(removed.status()).toBe(204);
    }
  }
});
