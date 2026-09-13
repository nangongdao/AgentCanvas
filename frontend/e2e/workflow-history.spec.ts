import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import {
  FRONTEND_URL,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
  logout,
} from "./support";

test("node edits support toolbar and keyboard undo redo with branch invalidation", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(
    request,
    `Workflow history ${Date.now()}-${test.info().workerIndex}`,
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );

    const nodes = page.locator(".react-flow__node");
    const nodeLibrary = page.locator("aside").filter({ hasText: "Node Library" });
    const addHumanNode = nodeLibrary.getByRole("button", { name: /人工/ });
    const undo = page.getByRole("button", { name: "撤销", exact: true });
    const redo = page.getByRole("button", { name: "重做", exact: true });

    const toolbarAccessibility = await new AxeBuilder({ page })
      .include('[role="toolbar"]')
      .analyze();
    expect(
      toolbarAccessibility.violations.filter((violation) =>
        ["critical", "serious"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);

    await expect(nodes).toHaveCount(2);
    await expect(undo).toBeDisabled();
    await expect(redo).toBeDisabled();

    await addHumanNode.click();
    await expect(nodes).toHaveCount(3);
    await expect(undo).toBeEnabled();

    await undo.click();
    await expect(nodes).toHaveCount(2);
    await expect(redo).toBeEnabled();

    await redo.click();
    await expect(nodes).toHaveCount(3);

    await page.keyboard.press("Control+z");
    await expect(nodes).toHaveCount(2);
    await page.keyboard.press("Control+y");
    await expect(nodes).toHaveCount(3);

    await undo.click();
    await expect(nodes).toHaveCount(2);
    await addHumanNode.click();
    await expect(nodes).toHaveCount(3);
    await expect(redo).toBeDisabled();
    await undo.click();
    await expect(nodes).toHaveCount(2);
    await expect(page.getByText("synced", { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("node drag and connected deletion each occupy one history step", async ({
  page,
  request,
}) => {
  // A physical multi-step drag plus undo/redo geometry polls sits close to
  // the 30s default under full-suite load; align with the soft-lock test.
  test.setTimeout(75_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(
    request,
    `Workflow history grouping ${Date.now()}-${test.info().workerIndex}`,
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );

    const nodes = page.locator(".react-flow__node");
    const edges = page.locator(".react-flow__edge");
    const endNode = page.locator('.react-flow__node[data-id="end"]');
    const undo = page.getByRole("button", { name: "撤销", exact: true });
    const redo = page.getByRole("button", { name: "重做", exact: true });
    const initialBox = await endNode.boundingBox();
    expect(initialBox).not.toBeNull();

    await page.mouse.move(
      initialBox!.x + 20,
      initialBox!.y + initialBox!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      initialBox!.x - 100,
      initialBox!.y + initialBox!.height / 2 + 70,
      { steps: 12 },
    );
    await page.mouse.up();
    await expect
      .poll(async () => (await endNode.boundingBox())?.x ?? initialBox!.x)
      .toBeLessThan(initialBox!.x - 60);

    await undo.click();
    await expect
      .poll(async () =>
        Math.abs(((await endNode.boundingBox())?.x ?? -100) - initialBox!.x),
      )
      .toBeLessThanOrEqual(2);
    await expect(undo).toBeDisabled();

    await redo.click();
    await expect
      .poll(async () => (await endNode.boundingBox())?.x ?? initialBox!.x)
      .toBeLessThan(initialBox!.x - 60);
    await undo.click();
    await expect(undo).toBeDisabled();

    await endNode.click();
    await page.keyboard.press("Delete");
    await expect(nodes).toHaveCount(1);
    await expect(edges).toHaveCount(0);

    await undo.click();
    await expect(nodes).toHaveCount(2);
    await expect(edges).toHaveCount(1);
    await expect(undo).toBeDisabled();

    await redo.click();
    await expect(nodes).toHaveCount(1);
    await expect(edges).toHaveCount(0);
    await undo.click();
    await expect(nodes).toHaveCount(2);
    await expect(edges).toHaveCount(1);
    await expect(page.getByText("synced", { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("workflow variables persist and participate in undo redo", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(
    request,
    `Workflow variables history ${Date.now()}-${test.info().workerIndex}`,
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );

    const variablesButton = page.locator('button[title="工作流变量"]');
    const undo = page.getByRole("button", { name: "撤销", exact: true });
    const redo = page.getByRole("button", { name: "重做", exact: true });
    await variablesButton.click();

    let dialog = page.getByRole("dialog", { name: "工作流变量" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('input[aria-label="变量名称"]').first()).toHaveValue(
      "user_query",
    );
    await dialog.getByRole("button", { name: "添加变量" }).click();
    const addedRow = dialog.locator('[data-variable-row]').last();
    await addedRow.getByLabel("变量名称").fill("priority");
    await addedRow.getByLabel("变量类型").selectOption("number");
    await addedRow.getByLabel("默认值").fill("3");
    await dialog.getByRole("button", { name: "应用变量" }).click();
    await expect(dialog).toBeHidden();
    await expect(undo).toBeEnabled();
    await expect(page.getByText("synced", { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    await undo.click();
    await variablesButton.click();
    dialog = page.getByRole("dialog", { name: "工作流变量" });
    await expect(dialog.locator('input[aria-label="变量名称"]').nth(1)).toHaveCount(0);
    await dialog.getByRole("button", { name: "取消" }).click();

    await redo.click();
    await variablesButton.click();
    dialog = page.getByRole("dialog", { name: "工作流变量" });
    await expect(dialog.locator('input[aria-label="变量名称"]').nth(1)).toHaveValue(
      "priority",
    );
    await dialog.getByRole("button", { name: "取消" }).click();

    await page.reload();
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );
    await expect(undo).toBeDisabled();
    await expect(redo).toBeDisabled();
    await page.setViewportSize({ width: 390, height: 844 });
    await variablesButton.click();
    dialog = page.getByRole("dialog", { name: "工作流变量" });
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox).not.toBeNull();
    expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
    expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(390);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    const dialogAccessibility = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .analyze();
    expect(
      dialogAccessibility.violations.filter((violation) =>
        ["critical", "serious"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);
    await expect(dialog.locator('input[aria-label="变量名称"]').nth(1)).toHaveValue(
      "priority",
    );
    await expect(dialog.locator('input[aria-label="默认值"]').nth(1)).toHaveValue(
      "3",
    );
    await dialog.getByRole("button", { name: "取消" }).click();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("history cannot bypass a transferred collaboration soft lock", async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(75_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(
    request,
    `Workflow history lock ${Date.now()}-${test.info().workerIndex}`,
  );
  const secondContext = await browser.newContext({ baseURL: FRONTEND_URL });
  const secondPage = await secondContext.newPage();
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );

    const startNode = page.locator('.react-flow__node[data-id="start"]');
    const undo = page.getByRole("button", { name: "撤销", exact: true });
    const initialBox = await startNode.boundingBox();
    expect(initialBox).not.toBeNull();
    const initialTransform = await startNode.evaluate(
      (element) => (element as HTMLElement).style.transform,
    );
    await page.mouse.move(
      initialBox!.x + initialBox!.width / 2,
      initialBox!.y + initialBox!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      initialBox!.x + initialBox!.width / 2 + 90,
      initialBox!.y + initialBox!.height / 2 + 60,
      { steps: 8 },
    );
    await page.mouse.up();
    const movedBox = await startNode.boundingBox();
    expect(movedBox).not.toBeNull();
    expect(movedBox!.x).toBeGreaterThan(initialBox!.x + 40);
    const movedTransform = await startNode.evaluate(
      (element) => (element as HTMLElement).style.transform,
    );
    expect(movedTransform).not.toBe(initialTransform);
    await expect(undo).toBeEnabled();

    await secondPage.goto(`/workflows/${workflow.id}`);
    await login(secondPage, "admin");
    await expect(secondPage.getByTestId("collaboration-takeover")).toBeVisible();
    await secondPage.getByTestId("collaboration-takeover").click();
    await expect(secondPage.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );
    await expect(undo).toBeDisabled();

    await page.keyboard.press("Control+z");
    await expect
      .poll(() =>
        startNode.evaluate((element) => (element as HTMLElement).style.transform),
      )
      .toBe(movedTransform);

    const leave = secondPage.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().includes(`/api/workflows/${workflow.id}/collaboration/presence/`),
    );
    await secondPage.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent("pagehide"));
    });
    expect((await leave).status()).toBe(200);
    await secondPage.close();

    await expect(page.getByTestId("collaboration-takeover")).toBeVisible();
    await page.getByTestId("collaboration-takeover").click();
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect
      .poll(() =>
        startNode.evaluate((element) => (element as HTMLElement).style.transform),
      )
      .toBe(initialTransform);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await secondContext.close();
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("history is bounded and resets across workflow and role boundaries", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const firstWorkflow = await createWorkflow(
    request,
    `Workflow history bound ${suffix}`,
  );
  const secondWorkflow = await createWorkflow(
    request,
    `Workflow history baseline ${suffix}`,
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${firstWorkflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );

    const undo = page.getByRole("button", { name: "撤销", exact: true });
    const redo = page.getByRole("button", { name: "重做", exact: true });
    const startNode = page.locator('.react-flow__node[data-id="start"]');
    await startNode.click();
    const labelInput = page
      .locator("label")
      .filter({ hasText: "显示名称" })
      .locator("input");
    const initialLabel = await labelInput.inputValue();

    await labelInput.press("End");
    await labelInput.pressSequentially("x".repeat(51));
    await expect(labelInput).toHaveValue(`${initialLabel}${"x".repeat(51)}`);
    for (let step = 0; step < 50; step += 1) await undo.click();

    await expect(labelInput).toHaveValue(`${initialLabel}x`);
    await expect(undo).toBeDisabled();
    await expect(redo).toBeEnabled();
    await expect(page.getByText("pending", { exact: true })).toBeVisible();
    await expect(page.getByText("synced", { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    await page.goto(`/workflows/${secondWorkflow.id}`);
    await expect(page.getByTestId("collaboration-status")).toContainText(
      "我在编辑",
    );
    await expect(undo).toBeDisabled();
    await expect(redo).toBeDisabled();

    await logout(page);
    await login(page, "viewer");
    await expect(page.getByRole("group", { name: "编辑历史" })).toHaveCount(0);
    await expect(page.locator('button[title="工作流变量"]')).toHaveCount(0);
    await expect(page.locator("aside").filter({ hasText: "Node Library" })).toHaveCount(0);

    const viewerStart = page.locator('.react-flow__node[data-id="start"]');
    const viewerTransform = await viewerStart.evaluate(
      (element) => (element as HTMLElement).style.transform,
    );
    await page.keyboard.press("Control+z");
    await expect
      .poll(() =>
        viewerStart.evaluate(
          (element) => (element as HTMLElement).style.transform,
        ),
      )
      .toBe(viewerTransform);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, firstWorkflow.id, bodyCompleted);
    await cleanupWorkflow(request, secondWorkflow.id, bodyCompleted);
  }
});
