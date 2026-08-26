import { expect, test } from "@playwright/test";

import {
  API_URL,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  editorHeaders,
  humanWorkflowBody,
  login,
} from "./support";

test("SSE reconnects after a network outage and resumes the live run", async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(120_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`SSE resilience ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    await page.getByRole("button", { name: "Fit View" }).click();

    const endNode = page.locator('[data-node-id="end"]');

    // Start the run; it pauses at the human node (a stable suspension point
    // whose state is replayed from the durable log on reconnect).
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    await expect(page.getByText("waiting_approval", { exact: true })).toBeVisible({
      timeout: 20_000,
    });

    // C6-3 weak-network chaos: cut the network while paused. EventSource
    // errors, ResilientSSE backs off exponentially and retries.
    await context.setOffline(true);
    await page.waitForTimeout(3_000);
    await context.setOffline(false);

    // After connectivity returns, the stream reconnects and replays the
    // durable waiting_approval snapshot — the approval panel must still be
    // interactive.
    await expect(page.getByText("waiting_approval", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "批准", exact: true }).click();
    await expect(page.getByText("succeeded", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    // Live events keep flowing after the outage: end turns succeeded green.
    await expect
      .poll(async () => {
        const cls = (await endNode.getAttribute("class")) ?? "";
        return { endSucceeded: cls.includes("border-ok") };
      }, { timeout: 15_000 })
      .toMatchObject({ endSucceeded: true });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (bodyCompleted) {
      await cleanupWorkflow(request, workflow.id, true);
    } else {
      // Best-effort cleanup even on failure paths.
      await request.delete(`${API_URL}/api/workflows/${workflow.id}`, {
        headers: editorHeaders,
      });
    }
  }
});
