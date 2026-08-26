import { expect, test } from "@playwright/test";

import { captureUnexpectedErrors, login } from "./support";

test("Node Library discovers and adds the process-isolated echo plugin", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/workflows/new");
  await login(page, "admin");

  const plugin = page.getByRole("button", { name: /Echo Plugin/ });
  await expect(plugin).toBeVisible();
  await expect(plugin).toContainText("process-isolated plugin");
  await plugin.click();

  await expect(page.locator('[data-node-id^="plugin.echo_"]')).toBeVisible();
  await expect(page.locator('[data-node-id^="plugin.echo_"]').getByText("plugin.echo", { exact: true })).toBeVisible();
  await expect(page.getByLabel("message")).toHaveValue("Hello from plugin");
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
