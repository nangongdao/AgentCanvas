import { expect, test } from "@playwright/test";

import { captureUnexpectedErrors, login, logout } from "./support";

test("admin can review MCP catalog versions and calculate a diff", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/mcp/catalog");
  await login(page, "admin");

  await expect(page.getByRole("heading", { name: "MCP Catalog Control" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Calculator/ })).toBeVisible();
  await expect(page.getByText("Version ledger", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "v1.0.0 builtin://calculator" }),
  ).toBeVisible();

  const target = page.getByLabel("Diff target version");
  await expect(target).toBeVisible();
  await page.getByRole("button", { name: /计算/ }).click();
  await expect(page.getByText(/NO CHANGES|source_ref|permissions/).first()).toBeVisible();
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("viewer cannot enter the MCP catalog administration page", async ({ page }) => {
  await page.goto("/mcp/catalog");
  await login(page, "viewer");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "MCP Catalog Control" })).toHaveCount(0);
  await logout(page);
});
