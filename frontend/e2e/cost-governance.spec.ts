import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  login,
} from "./support";

async function pollExecution(request: import("@playwright/test").APIRequestContext, id: string) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const response = await request.get(`${API_URL}/api/executions/${id}`, {
      headers: adminHeaders,
    });
    const row = (await response.json()) as { status: string };
    if (row.status !== "running" && row.status !== "queued" && row.status !== "waiting") {
      return row;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("execution did not finish");
}

test("cost governance page renders ceilings and alert summary", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/cost");
  await login(page, "admin");

  await expect(page.getByRole("heading", { name: "成本治理", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "单次执行上限", exact: true })).toBeVisible();
  await expect(page.getByText("token / 执行")).toBeVisible();
  await expect(page.getByText("费用上限 / 执行")).toBeVisible();
  await expect(page.getByRole("heading", { name: "告警汇总", exact: true })).toBeVisible();
  await expect(page.getByText("调用次数 / 执行")).toBeVisible();

  // C5-11: the cost surface carries no critical/serious axe violations.
  const axe = await new AxeBuilder({ page }).analyze();
  expect(
    axe.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? "")),
  ).toEqual([]);

  expect(errors.pageErrors).toEqual([]);
});

test("cost ceiling breach surfaces a durable alert and can be acknowledged", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);

  // Point the default model at the mock provider with pricing so the
  // execution budget can price calls against the tiny cost ceiling.
  const updated = await request.put(`${API_URL}/api/models/default`, {
    headers: adminHeaders,
    data: {
      provider: "mock",
      prompt_price_per_million_usd: "10",
      completion_price_per_million_usd: "20",
      pricing_version: "price-2026-01",
    },
  });
  expect(updated.status()).toBe(200);

  const started = await request.post(`${API_URL}/api/workflows/demo-linear/run`, {
    headers: adminHeaders,
    data: { inputs: { user_query: "hi" } },
  });
  expect(started.status()).toBe(201);
  const executionId = ((await started.json()) as { id: string }).id;
  const finished = await pollExecution(request, executionId);
  expect(finished.status).toBe("failed");

  await page.goto("/cost");
  await login(page, "admin");

  const alertRow = page.getByText("cost budget exceeded");
  await expect(alertRow).toBeVisible();

  await page.getByRole("button", { name: "确认", exact: true }).first().click();
  await expect(page.getByRole("button", { name: "确认", exact: true })).toHaveCount(0);

  expect(errors.pageErrors).toEqual([]);
});
