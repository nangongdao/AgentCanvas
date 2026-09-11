import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

/**
 * The model dialog probes the endpoint's own model list so an operator picks a
 * real id instead of typing one. Uses the demo (mock) provider, so the happy path
 * needs no credentials and no network.
 */
test("model dialog discovers an endpoint's models and fills the picker", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const modelId = `discover-browser-${suffix}`;
  const modelName = `Discover Browser ${suffix}`;
  let created = false;
  try {
    const response = await request.post(`${API_URL}/api/models`, {
      headers: adminHeaders,
      data: {
        id: modelId,
        name: modelName,
        provider: "mock",
        model_name: "mock-chat",
      },
    });
    expect(response.status()).toBe(201);
    created = true;

    await page.goto("/models");
    await login(page, "admin");
    const modelRow = page.locator("article").filter({ hasText: modelName });
    await expect(modelRow).toBeVisible();
    await modelRow.getByRole("button", { name: "编辑" }).click();

    const dialog = page.getByRole("dialog", { name: "编辑模型" });

    // The demo provider answers from a canned catalog: connection succeeds and
    // both models are offered.
    await dialog.getByRole("button", { name: "测试连接并获取模型" }).click();
    const status = dialog.getByTestId("model-discovery-status");
    await expect(status).toContainText("连接正常");
    await expect(status).toContainText("2 个模型");

    // Picking one fills model_name and adopts the inferred type.
    await dialog.getByLabel("从发现的模型中选择").selectOption("mock-embed");
    await expect(dialog.getByLabel("模型名 (model_name)", { exact: true })).toHaveValue(
      "mock-embed",
    );
    // Selects are addressed by role: getByLabel reads the wrapping label's raw
    // text, which absorbs the option texts and defeats an exact match.
    await expect(
      dialog.getByRole("combobox", { name: "类型", exact: true }),
    ).toHaveValue("embedding");

    // A public-IP-only guard rejects a loopback target until the operator opts in,
    // and the reason is surfaced inline rather than swallowed.
    await dialog
      .getByRole("combobox", { name: "Provider", exact: true })
      .selectOption("ollama");
    await dialog.getByLabel("Base URL（可选）", { exact: true }).fill("http://127.0.0.1:11434");
    await dialog.getByRole("button", { name: "测试连接并获取模型" }).click();
    await expect(dialog.getByTestId("model-discovery-error")).toContainText("public endpoint");

    await dialog.getByRole("button", { name: "关闭" }).click();
    await expect(dialog).toBeHidden();

    // Probing is read-only: nothing was written until 保存.
    const listed = await request.get(`${API_URL}/api/models`, { headers: adminHeaders });
    expect(listed.status()).toBe(200);
    const stored = (await listed.json()) as Array<{ id: string; model_name: string }>;
    expect(stored.find((item) => item.id === modelId)?.model_name).toBe("mock-chat");

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    // The loopback probe above is expected to answer 400; every other response
    // must be clean.
    expect(
      errors.httpErrors.filter((entry) => !entry.includes("/api/models/discover")),
    ).toEqual([]);
  } finally {
    if (created) {
      const removed = await request.delete(`${API_URL}/api/models/${modelId}`, {
        headers: adminHeaders,
      });
      expect(removed.status()).toBe(204);
    }
  }
});
