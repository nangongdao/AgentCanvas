import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { cleanupWorkflow, createWorkflow, login } from "./support";

test("global command palette opens from the shell and navigates search results by keyboard", async ({
  page,
  request,
}, testInfo) => {
  const workflowName = `Palette Workflow ${Date.now()}`;
  const workflow = await createWorkflow(request, workflowName);
  let bodyCompleted = false;
  try {
    await page.goto("/knowledge");
    await expect(page.locator("#auth-dialog-title")).toBeVisible();
    await login(page, "admin");

    const opener = page.getByRole("button", { name: "搜索和命令" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "搜索和命令" });
    const input = dialog.getByRole("combobox", { name: "搜索资源或输入命令" });
    await expect(dialog).toBeVisible();
    await expect(input).toBeFocused();
    await expect(dialog.getByRole("option", { name: /新建工作流/ })).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("option", { name: /审计日志/ })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(input).toBeFocused();

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(
      accessibility.violations.filter((violation) =>
        ["critical", "serious"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("command-palette-desktop.png") });

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    await page.keyboard.press("Control+k");
    await expect(input).toBeFocused();
    await input.fill(workflowName);
    const result = dialog.getByRole("option", { name: new RegExp(workflowName) });
    await expect(result).toBeVisible();
    await page.keyboard.press("Home");
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(new RegExp(`/workflows/${workflow.id}$`));
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("application results focus the exact app on its project page", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");

  await page.route("**/api/search?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        query: "Focused Application",
        items: [
          {
            kind: "app",
            id: "app-focused",
            title: "Focused Application",
            subtitle: "slug: focused-application",
            project_id: "project-focused",
            parent_id: null,
          },
        ],
      }),
    }),
  );
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "project-focused",
          organization_id: "org-focused",
          name: "Focused Project",
          slug: "focused-project",
        },
      ]),
    }),
  );
  await page.route("**/api/apps?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        next_cursor: null,
        has_more: false,
      }),
    }),
  );
  await page.route("**/api/apps/app-focused", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "app-focused",
        project_id: "project-focused",
        workflow_id: null,
        published_version_id: null,
        published_version_number: null,
        name: "Focused Application",
        icon: null,
        type: "chatbot",
        welcome_message: null,
        suggested_questions: [],
        input_form: [],
        visibility: "project",
        status: "active",
        has_public_access: false,
        token_prefix: null,
        slug: "focused-application",
        theme_color: null,
        embed_allowed_origins: null,
        created_at: "2026-08-19T00:00:00Z",
        updated_at: "2026-08-19T00:00:00Z",
      }),
    }),
  );

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  await dialog
    .getByRole("combobox", { name: "搜索资源或输入命令" })
    .fill("Focused Application");
  await dialog.getByRole("option", { name: /Focused Application/ }).click();

  await expect(page).toHaveURL(
    /\/apps\?app_id=app-focused&project_id=project-focused$/,
  );
  await expect(page.locator('[data-app-id="app-focused"]')).toBeFocused();
});

test("safe commands execute while privileged destinations stay hidden from viewers", async ({
  page,
  context,
}) => {
  await page.goto("/knowledge");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "viewer");
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: new URL(page.url()).origin,
  });

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  const opener = page.getByRole("button", { name: "搜索和命令" });
  await expect(dialog.getByRole("option", { name: /新建工作流/ })).toBeHidden();
  await dialog.getByRole("option", { name: /复制当前页面链接/ }).click();
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
    page.url(),
  );

  await page.keyboard.press("Control+k");
  await expect(dialog.getByRole("option", { name: /MCP 管理/ })).toBeHidden();
  await expect(dialog.getByRole("option", { name: /审计日志/ })).toBeHidden();

  const selected = dialog.locator('[role="option"][aria-selected="true"]');
  await page.keyboard.press("End");
  await expect(selected).toContainText("项目配额");
  await page.keyboard.press("Home");
  await expect(selected).toContainText("复制当前页面链接");
  await page.keyboard.press("ArrowDown");
  await expect(selected).toContainText("运营概览");
  await page.keyboard.press("ArrowDown");
  await expect(selected).toContainText("知识库");
  await page.keyboard.press("ArrowUp");
  await expect(selected).toContainText("运营概览");

  await dialog.getByRole("option", { name: /应用发布/ }).click();
  await expect(page).toHaveURL(/\/apps$/);
  await expect(opener).toBeFocused();
});

test("editor command selection resets an existing new-workflow draft", async ({ page }) => {
  await page.goto("/workflows/new");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "editor");

  const workflowName = page.getByTitle("工作流名称");
  await expect(workflowName).toHaveValue("未命名工作流");
  await workflowName.fill("需要重置的草稿");

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  await dialog.getByRole("option", { name: /新建工作流/ }).click();
  const unsaved = page.getByRole("alertdialog", { name: "当前修改尚未保存" });
  const discard = unsaved.getByRole("button", { name: "放弃并离开" });
  await expect
    .poll(async () => {
      if (await discard.isVisible()) {
        await discard.click({ timeout: 500 }).catch(() => undefined);
      }
      return workflowName.inputValue();
    })
    .toBe("未命名工作流");
  await expect(page).toHaveURL(/\/workflows\/new$/);
  await expect(dialog).toBeHidden();
});

test("document results keep their knowledge-base navigation context", async ({ page }) => {
  await page.goto("/knowledge");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");
  await page.route("**/api/search?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        query: "incident-runbook.pdf",
        items: [
          {
            kind: "document",
            id: "document-target",
            title: "incident-runbook.pdf",
            subtitle: "Operations Knowledge",
            project_id: "project-target",
            parent_id: "knowledge-target",
          },
        ],
      }),
    }),
  );

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  await dialog
    .getByRole("combobox", { name: "搜索资源或输入命令" })
    .fill("incident-runbook.pdf");
  await dialog.getByRole("option", { name: /incident-runbook\.pdf/ }).click();

  await expect(page).toHaveURL(
    /\/knowledge\?kb_id=knowledge-target&document_id=document-target$/,
  );
});

test("search exposes loading, empty, error, and retry states", async ({ page }) => {
  await page.goto("/knowledge");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");

  let releaseDelayed = () => {};
  const delayed = new Promise<void>((resolve) => {
    releaseDelayed = resolve;
  });
  let offlineAttempts = 0;
  await page.route("**/api/search?*", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q");
    if (query === "delayed-resource") {
      await delayed;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ query, items: [] }),
      });
      return;
    }
    offlineAttempts += 1;
    await route.fulfill({
      status: offlineAttempts === 1 ? 503 : 200,
      contentType: "application/json",
      body: JSON.stringify(
        offlineAttempts === 1
          ? { detail: "搜索服务暂不可用" }
          : { query, items: [] },
      ),
    });
  });

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  const input = dialog.getByRole("combobox", { name: "搜索资源或输入命令" });
  await input.fill("delayed-resource");
  await expect(
    dialog.getByRole("listbox").getByText("正在搜索", { exact: true }),
  ).toBeVisible();
  releaseDelayed();
  await expect(dialog.getByText("没有匹配的页面或资源", { exact: true })).toBeVisible();

  await input.fill("offline-resource");
  await expect(dialog.getByText("搜索服务暂不可用", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "重试" }).click();
  await expect(dialog.getByText("没有匹配的页面或资源", { exact: true })).toBeVisible();
  expect(offlineAttempts).toBe(2);
});

test("a late response cannot replace results for the current query", async ({ page }) => {
  await page.goto("/knowledge");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");

  await page.route("**/api/search?*", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") ?? "";
    if (query === "slow-query") {
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    try {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          query,
          items: [
            {
              kind: "workflow",
              id: query === "slow-query" ? "stale-workflow" : "fresh-workflow",
              title: query === "slow-query" ? "Stale Workflow" : "Fresh Workflow",
              subtitle: "",
              project_id: null,
              parent_id: null,
            },
          ],
        }),
      });
    } catch {
      // The browser may cancel the superseded request before the mocked response is released.
    }
  });

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  const input = dialog.getByRole("combobox", { name: "搜索资源或输入命令" });
  const slowRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/search" && url.searchParams.get("q") === "slow-query";
  });
  await input.fill("slow-query");
  await slowRequest;
  await input.fill("fresh-query");
  await expect(dialog.getByRole("option", { name: /Stale Workflow/ })).toBeHidden();
  await expect(
    dialog.getByRole("listbox").getByText("正在搜索", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole("option", { name: /Fresh Workflow/ })).toBeVisible();
  await page.waitForTimeout(800);
  await expect(dialog.getByRole("option", { name: /Stale Workflow/ })).toBeHidden();
  await expect(dialog.getByRole("option", { name: /Fresh Workflow/ })).toBeVisible();
});

test("mobile palette stays inside the viewport and passes serious axe checks", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 500 });
  await page.goto("/");
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");

  await page.getByRole("button", { name: "搜索和命令" }).click();
  const dialog = page.getByRole("dialog", { name: "搜索和命令" });
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.y + box!.height).toBeLessThanOrEqual(500);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.keyboard.press("End");
  await expect(dialog.getByRole("option", { name: /审计日志/ })).toBeInViewport();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) =>
      ["critical", "serious"].includes(violation.impact ?? ""),
    ),
  ).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("command-palette-mobile.png") });
});
