import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
  workflowBody,
} from "./support";

test("light theme applies surface tokens and persists across reload", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    workflowBody(`Theme light ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");

    // Open the account/appearance menu from the shell header and pick 亮色.
    const menuTrigger = page.locator('button[title="当前访问角色与偏好"]');
    await menuTrigger.click();
    const themeGroup = page.getByRole("radiogroup", { name: "主题" });
    await expect(themeGroup).toBeVisible();
    const lightRadio = themeGroup.getByRole("radio", { name: "亮色" });
    await lightRadio.click();

    // The root html element carries data-theme="light" once the light token
    // set applies, and the body background resolves to the light void.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const bg = await page.evaluate(() => {
      const el = document.documentElement;
      return getComputedStyle(el).getPropertyValue("--void").trim();
    });
    expect(bg.length).toBeGreaterThan(0);

    // The canvas pane background follows the light token. Wait for the canvas
    // to mount first — the workflow DSL load is asynchronous.
    await expect(page.locator(".react-flow")).toBeVisible();
    const paneBg = await page.evaluate(() => {
      const pane = document.querySelector(".react-flow");
      if (!(pane instanceof HTMLElement)) return null;
      return window.getComputedStyle(pane).backgroundColor;
    });
    expect(paneBg).not.toBeNull();

    // Let the theme-change transitions (node/palette `transition-all
    // duration-300`) settle before the a11y snapshot — axe blends
    // mid-transition colors and would produce false contrast failures.
    await page.waitForTimeout(800);
    const axe = await new AxeBuilder({ page })
      .exclude(".react-flow__edge")
      .analyze();
    expect(
      axe.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    // Preference persists across a full reload (localStorage round-trip).
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    // The selected radio reflects the persisted state.
    await menuTrigger.click();
    await expect(
      page.getByRole("radiogroup", { name: "主题" }).getByRole("radio", { name: "亮色" }),
    ).toHaveAttribute("aria-checked", "true");

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("system theme follows prefers-color-scheme and switches live", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    workflowBody(`Theme system ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");

    const menuTrigger = page.locator('button[title="当前访问角色与偏好"]');
    await menuTrigger.click();
    await page
      .getByRole("radiogroup", { name: "主题" })
      .getByRole("radio", { name: "跟随系统" })
      .click();

    // Emulate dark OS preference → effective dark (no data-theme attr).
    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "light");

    // Flip OS to light → effective light applies live without a reload.
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
