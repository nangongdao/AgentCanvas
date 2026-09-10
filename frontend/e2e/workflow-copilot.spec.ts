import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
  workflowBody,
} from "./support";

/**
 * The copilot is exercised through the demo (mock) Provider, which needs no API
 * key, so this spec covers the real HTTP path end to end: prompt -> validated
 * draft -> applied to the canvas -> undoable.
 */
test("copilot drafts a workflow from a prompt and applies it as one undoable change", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    workflowBody(`Copilot canvas ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");

    // The starter workflow has exactly two nodes; the draft adds an agent.
    await expect(page.locator(".react-flow__node")).toHaveCount(2);

    await page.getByRole("button", { name: "AI 副驾", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "AI 副驾" });
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("你想要什么工作流?").fill("做一个带 Agent 的问答工作流");
    await dialog.getByRole("button", { name: "生成草稿" }).click();

    await expect(dialog.getByText("已通过校验")).toBeVisible({ timeout: 20_000 });
    await expect(dialog.getByText("3 个节点")).toBeVisible();

    await dialog.getByRole("button", { name: "应用到画布" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(".react-flow__node")).toHaveCount(3);

    // Applying is an ordinary history entry, not a baseline reset, so a single
    // undo restores the canvas the user had before the draft.
    await page.keyboard.press("Control+z");
    await expect(page.locator(".react-flow__node")).toHaveCount(2);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
