import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
} from "./support";

test("workflow versions publish, compare, rollback, clone, and identify executions", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const originalName = `Versions E2E ${Date.now()}`;
  const workflow = await createWorkflow(request, originalName);
  let cloneId = "";
  let importedId = "";
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    const nameInput = page.locator('input[placeholder="工作流名称"]');
    const commandBar = page.getByRole("toolbar", { name: "工作流命令栏" });
    await expect(nameInput).toHaveValue(originalName);

    const firstSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(`/api/workflows/${workflow.id}`),
    );
    await nameInput.fill(`${originalName} draft`);
    expect((await firstSave).status()).toBe(200);
    await expect(commandBar.getByText("synced", { exact: true })).toBeVisible();
    await expect(commandBar).toContainText("v2");

    await page.locator('button[title="版本历史"]').click();
    let dialog = page.getByRole("dialog", { name: "版本历史" });
    await expect(dialog.getByText("v2", { exact: true })).toBeVisible();
    await expect(dialog.getByText("v1", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "发布", exact: true }).click();
    await expect(dialog.getByText("published", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();

    const secondSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(`/api/workflows/${workflow.id}`),
    );
    await nameInput.fill(`${originalName} next`);
    expect((await secondSave).status()).toBe(200);
    await expect(commandBar.getByText("synced", { exact: true })).toBeVisible();
    await expect(commandBar).toContainText("v3");

    await page.locator('button[title="版本历史"]').click();
    dialog = page.getByRole("dialog", { name: "版本历史" });
    await expect(dialog.getByText("v3", { exact: true })).toBeVisible();
    await expect(dialog.getByText("published", { exact: true })).toBeVisible();
    await dialog.getByLabel("选择版本 2 用于对比").click();
    await dialog.getByLabel("选择版本 3 用于对比").click();
    await expect(dialog.getByText("Version diff", { exact: true })).toBeVisible();
    await expect(dialog.getByText("节点变更", { exact: true })).toBeVisible();

    page.once("dialog", (confirmation) => void confirmation.accept());
    const versionOne = dialog.locator("li").filter({ hasText: /^v1/ });
    await versionOne.getByRole("button", { name: "恢复", exact: true }).click();
    await expect(nameInput).toHaveValue(originalName);
    await expect(commandBar).toContainText("v4");

    const versionTwo = dialog.locator("li").filter({ hasText: /^v2/ });
    const downloadPromise = page.waitForEvent("download");
    await versionTwo.getByRole("button", { name: "导出版本 2" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain("-v2.json");
    const exportedPath = await download.path();
    expect(exportedPath).not.toBeNull();

    const cloneNavigation = page.waitForURL(
      (url) =>
        /^\/workflows\/[a-f0-9]{32}$/.test(url.pathname) &&
        !url.pathname.endsWith(workflow.id),
    );
    await versionTwo.getByRole("button", { name: "克隆", exact: true }).click();
    await cloneNavigation;
    cloneId = page.url().split("/").at(-1) ?? "";
    expect(cloneId).not.toBe(workflow.id);
    await expect(nameInput).toHaveValue(`${originalName} draft (Copy)`);

    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await runDialog.locator("input").first().fill("versioned execution");
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    await expect(page.getByText("workflow_finished", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    await page.locator('button[title="执行历史"]').click();
    await expect(page.getByText("v1", { exact: true }).last()).toBeVisible();

    await page.locator('button[title="打开工作流"]').click();
    const navigator = page.getByRole("dialog", { name: "工作流目录" });
    const importResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/workflows/import"),
    );
    await navigator.getByLabel("选择工作流 JSON").setInputFiles(exportedPath!);
    const imported = await importResponse;
    expect(imported.status()).toBe(201);
    importedId = ((await imported.json()) as { id: string }).id;
    await page.waitForURL(new RegExp(`/workflows/${importedId}$`));
    await expect(nameInput).toHaveValue(`${originalName} draft`);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('button[title="版本历史"]').click();
    dialog = page.getByRole("dialog", { name: "版本历史" });
    await expect(dialog).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (importedId) await cleanupWorkflow(request, importedId, bodyCompleted);
    if (cloneId) await cleanupWorkflow(request, cloneId, bodyCompleted);
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
