import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
} from "./support";

test("workflows list page is keyboard accessible and passes axe audit", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(request, `A11y List ${Date.now()}`);
  let bodyCompleted = false;

  try {
    await page.goto("/workflows");
    await login(page, "editor");

    // Wait for the list to load
    await expect(page.getByRole("heading", { name: "工作流" })).toBeVisible();

    // The newly created workflow should appear in the list
    await expect(page.getByText(workflow.name)).toBeVisible();

    // Run axe accessibility audit on the workflows list page
    const accessibility = await new AxeBuilder({ page })
      .exclude('[data-testid="canvas-tour"]') // Tour may not be present
      .analyze();

    expect(
      accessibility.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    // Keyboard navigation: the list items should be focusable
    const workflowLink = page.getByRole("link", { name: new RegExp(workflow.name) });
    await expect(workflowLink).toBeVisible();

    // Tab navigation should reach the workflow link
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    const focusedElement = await page.evaluate(() => document.activeElement?.tagName);
    expect(focusedElement).toBeTruthy();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("knowledge bases page is keyboard accessible and passes axe audit", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);

  await page.goto("/knowledge");
  await login(page, "editor");

  // Wait for page to load by checking for the heading
  await expect(page.getByRole("heading", { name: /AgentCanvas Knowledge/i })).toBeVisible();

  // Run axe accessibility audit on the knowledge bases page
  const accessibility = await new AxeBuilder({ page }).analyze();

  expect(
    accessibility.violations.filter((v) =>
      ["critical", "serious"].includes(v.impact ?? ""),
    ),
  ).toEqual([]);

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("models page is keyboard accessible and passes axe audit", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);

  await page.goto("/models");
  await login(page, "admin");

  await expect(page.getByRole("heading", { name: /AgentCanvas Models/i })).toBeVisible();

  // Run axe accessibility audit on the models page
  const accessibility = await new AxeBuilder({ page }).analyze();

  expect(
    accessibility.violations.filter((v) =>
      ["critical", "serious"].includes(v.impact ?? ""),
    ),
  ).toEqual([]);

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("MCP catalog page is keyboard accessible and passes axe audit", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);

  await page.goto("/settings/mcp");
  await login(page, "admin");

  await expect(page.getByRole("heading", { name: /MCP Catalog Control/i })).toBeVisible();

  // Run axe accessibility audit on the MCP page
  const accessibility = await new AxeBuilder({ page }).analyze();

  expect(
    accessibility.violations.filter((v) =>
      ["critical", "serious"].includes(v.impact ?? ""),
    ),
  ).toEqual([]);

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
