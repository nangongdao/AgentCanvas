import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
  workflowBody,
} from "./support";

test("workflow navigator empty state offers guidance and one-click example creation", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);

  // Deterministic empty list: stub only the navigator's list call. The
  // instantiate POST and the subsequent workflow load hit the real backend.
  await page.route("**/api/workflows?*", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
    });
  });

  try {
    await page.goto("/workflows/new");
    await login(page, "editor");
    // An unsaved blank canvas has no collaboration indicator yet; the
    // command bar marks the editor surface as ready.
    await expect(
      page.getByRole("toolbar", { name: "工作流命令栏" }),
    ).toBeVisible();

    // A first-in-browser editor meets the canvas tour; skip it to reach the
    // toolbar underneath.
    const tour = page.getByTestId("canvas-tour");
    if (await tour.isVisible().catch(() => false)) {
      await tour.getByRole("button", { name: "跳过" }).click();
      await expect(tour).toBeHidden();
    }

    await page.locator('button[title="打开工作流"]').click();
    const navigator = page.locator('[aria-labelledby="workflow-navigator-title"]');
    await expect(navigator).toBeVisible();

    // The guidance card replaces the bare "no workflows" text for editors.
    const card = page.getByTestId("workflow-onboarding-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("开始构建你的第一个工作流");
    const accessibility = await new AxeBuilder({ page })
      .include('[aria-labelledby="workflow-navigator-title"]')
      .analyze();
    expect(
      accessibility.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    // One click instantiates the official starter template (its single
    // parameter has a default) and opens the created workflow.
    const created = page.waitForResponse(
      (response) =>
        response.url().includes("/api/workflow-templates/official-linear/instantiate") &&
        response.request().method() === "POST",
    );
    await card.getByRole("button", { name: "从示例创建" }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const workflow = (await response.json()) as { id: string; name: string };
    expect(workflow.name).toContain("Linear");

    await expect(navigator).toBeHidden();
    await expect(page).toHaveURL(new RegExp(`/workflows/${workflow.id}$`));
    await expect(page.locator(".react-flow__node").first()).toBeVisible();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    await cleanupWorkflow(page.request, workflow.id, true);
  } finally {
    await page.unroute("**/api/workflows?*").catch(() => undefined);
  }
});

test("canvas tour walks three steps once and stays dismissed", async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    workflowBody(`Onboarding tour ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    // Log in inline instead of via support login(), which seeds the
    // suite-wide tour dismissal — this test needs a genuine first visit.
    await page.locator("#auth-dialog-title").waitFor();
    await page.getByRole("button", { name: "API Token", exact: true }).click();
    await page.getByLabel("API Token").fill("admin-e2e-token-20260729");
    await Promise.all([
      page.waitForEvent("load"),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);

    const tour = page.getByTestId("canvas-tour");
    await expect(tour).toBeVisible();
    await expect(tour).toContainText("画布快速上手");
    await expect(tour).toContainText("添加节点");
    await expect(tour).toContainText("第 1 / 3 步");

    const tourAccessibility = await new AxeBuilder({ page })
      .include('[data-testid="canvas-tour"]')
      .analyze();
    expect(
      tourAccessibility.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    // Arrow keys walk the steps; the final step completes and persists.
    await page.keyboard.press("ArrowRight");
    await expect(tour).toContainText("连接与整理");
    await page.keyboard.press("ArrowLeft");
    await expect(tour).toContainText("添加节点");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(tour).toContainText("运行与发布");
    await tour.getByRole("button", { name: "完成" }).click();
    await expect(tour).toBeHidden();
    expect(
      await page.evaluate(() => localStorage.getItem("agentcanvas:canvas-tour")),
    ).toBe("done");

    // Completion survives reloads — the session cookie keeps the login, so
    // no re-login is needed and the tour must not interrupt again.
    await page.reload();
    await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("admin");
    await expect(page.getByTestId("canvas-tour")).toHaveCount(0);

    // A dismissed tour also stays gone via the Escape/skip path.
    await page.evaluate(() => localStorage.removeItem("agentcanvas:canvas-tour"));
    await page.reload();
    await expect(page.getByTestId("canvas-tour")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("canvas-tour")).toBeHidden();
    await page.reload();
    await expect(page.getByTestId("canvas-tour")).toHaveCount(0);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("knowledge sidebar empty state guides creation", async ({ page }) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);

  await page.route("**/api/knowledge-bases*", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
    });
  });

  try {
    await page.goto("/knowledge");
    await login(page, "editor");

    const card = page.getByTestId("knowledge-onboarding-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("开始沉淀团队知识");

    await card.getByRole("button", { name: "新建知识库" }).click();
    await expect(page.locator("#knowledge-dialog-title")).toBeVisible();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
  } finally {
    await page.unroute("**/api/knowledge-bases*").catch(() => undefined);
  }
});
