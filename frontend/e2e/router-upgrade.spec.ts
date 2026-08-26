import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
} from "./support";

test("Router 7 preserves deep links and dirty-navigation decisions", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflow(request, `Router 7 E2E ${Date.now()}`);
  const workflowUrl = `/workflows/${workflow.id}`;
  let bodyCompleted = false;

  try {
    await page.goto(workflowUrl);
    await expect(page.locator("#auth-dialog-title")).toBeVisible();
    await login(page, "editor");
    await expect(page).toHaveURL(new RegExp(`${workflowUrl}$`));

    const nameInput = page.locator('input[placeholder="工作流名称"]');
    const knowledgeLink = page.locator('a[title="知识库"]');
    const unsavedDialog = page.getByRole("alertdialog", {
      name: "当前修改尚未保存",
    });

    await nameInput.fill(`Router 7 stay ${Date.now()}`);
    await knowledgeLink.click();
    await expect(unsavedDialog).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${workflowUrl}$`));

    await unsavedDialog.getByRole("button", { name: "留在此页" }).click();
    await expect(unsavedDialog).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`${workflowUrl}$`));

    await nameInput.fill(`Router 7 discard ${Date.now()}`);
    await knowledgeLink.click();
    await expect(unsavedDialog).toBeVisible();
    await unsavedDialog.getByRole("button", { name: "放弃并离开" }).click();
    await expect(page).toHaveURL(/\/knowledge$/);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
