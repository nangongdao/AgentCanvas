import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  humanWorkflowBody,
  login,
} from "./support";

test("queued nodes turn amber while waiting, then succeed after approval", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`Node UX queue ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");
    await page.getByRole("button", { name: "Fit View" }).click();

    const endNode = page.locator('[data-node-id="end"]');

    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await runDialog.getByRole("button", { name: "开始运行" }).click();

    // The run pauses at the human node (waiting_approval). While paused, the
    // downstream `end` node has a started predecessor (the human node, which
    // emitted node_started before interrupting) but has not started itself, so
    // the queue derivation marks it queued (amber dot + warn border). This is
    // a stable state — it holds for as long as approval is pending.
    await expect(page.getByText("waiting_approval", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect.poll(async () => {
      const cls = (await endNode.getAttribute("class")) ?? "";
      return { endQueued: cls.includes("border-warn") };
    }, { timeout: 10_000 }).toMatchObject({ endQueued: true });

    // Approving resumes the run; the end node then runs and succeeds.
    await page.getByRole("button", { name: "批准", exact: true }).click();
    await expect(page.getByText("succeeded", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect.poll(async () => {
      const cls = (await endNode.getAttribute("class")) ?? "";
      return { endSucceeded: cls.includes("border-ok") };
    }, { timeout: 15_000 }).toMatchObject({ endSucceeded: true });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
