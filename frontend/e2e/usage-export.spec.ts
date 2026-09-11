import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { API_URL, adminHeaders, captureUnexpectedErrors, login } from "./support";

const DAY_MS = 86_400_000;

function isoDay(offsetDays: number): string {
  return new Date(Date.now() + offsetDays * DAY_MS).toISOString().slice(0, 10);
}

/**
 * C7-1 surface on the cost governance page: the platform-wide usage export
 * (the billing hand-off) and the deterministic reconciliation digest. This
 * runs under the default config so CI covers it — `cost-governance.spec.ts`
 * stays on its own runner because it needs a deliberately tiny per-execution
 * cost ceiling.
 */
test("cost page exports usage facts as a file and reads the reconciliation digest", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const today = isoDay(0);
  const windowStart = isoDay(-29);

  // A finished execution gives the window at least one priced-or-not fact.
  const started = await request.post(`${API_URL}/api/workflows/demo-linear/run`, {
    headers: adminHeaders,
    data: { inputs: { user_query: "usage export" } },
  });
  expect(started.status()).toBe(201);
  const executionId = ((await started.json()) as { id: string }).id;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const row = await request.get(`${API_URL}/api/executions/${executionId}`, {
      headers: adminHeaders,
    });
    const status = ((await row.json()) as { status: string }).status;
    if (status !== "running" && status !== "queued" && status !== "waiting") break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  await page.goto("/cost");
  await login(page, "admin");

  await expect(
    page.getByRole("heading", { name: "用量导出与对账", exact: true }),
  ).toBeVisible();

  // CSV is the default; the filename is derived server-side from scope+window.
  const csvDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出", exact: true }).click();
  expect((await csvDownload).suggestedFilename()).toBe(
    `usage-all-${windowStart}-${today}.csv`,
  );
  await expect(page.getByText(/已导出 usage-all-/)).toBeVisible();

  // The other serialization keeps the same scope and window.
  await page.getByRole("combobox", { name: "格式" }).selectOption("json");
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出", exact: true }).click();
  expect((await jsonDownload).suggestedFilename()).toBe(
    `usage-all-${windowStart}-${today}.json`,
  );

  // The digest is deterministic and its title carries the full hash, so the
  // truncated on-screen value can be checked against the API verbatim.
  const month = today.slice(0, 7);
  const direct = await request.get(
    `${API_URL}/api/usage/reconciliation?month=${month}`,
    { headers: adminHeaders },
  );
  expect(direct.status()).toBe(200);
  const apiDigest = ((await direct.json()) as { digest: string }).digest;

  await page.getByRole("button", { name: "获取对账摘要", exact: true }).click();
  const digest = page.getByTestId("usage-digest");
  await expect(digest).toBeVisible();
  await expect(digest).toHaveAttribute("title", apiDigest);
  // Platform-wide default: no organization/project scope is applied.
  await expect(page.getByText("全平台", { exact: true })).toBeVisible();

  // A repeated poll of an unchanged month must yield an unchanged digest —
  // that idempotence is what lets a billing system poll safely.
  const repeat = await request.get(
    `${API_URL}/api/usage/reconciliation?month=${month}`,
    { headers: adminHeaders },
  );
  expect(repeat.status()).toBe(200);
  expect(((await repeat.json()) as { digest: string }).digest).toBe(apiDigest);

  // A reversed window is refused client-side rather than 422-ing the server.
  await page.getByLabel("起始").fill(today);
  await page.getByLabel("结束").fill(isoDay(-1));
  await page.getByRole("button", { name: "导出", exact: true }).click();
  await expect(page.getByText("起始日期不能晚于结束日期")).toBeVisible();

  const axe = await new AxeBuilder({ page }).analyze();
  expect(
    axe.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? "")),
  ).toEqual([]);

  expect(errors.pageErrors).toEqual([]);
});
