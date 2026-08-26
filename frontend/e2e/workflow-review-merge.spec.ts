import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
} from "./support";

test("version comments, review decisions, and disjoint conflict merge stay recoverable", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const originalName = `Review merge E2E ${Date.now()}`;
  const localName = `${originalName} local`;
  const workflow = await createWorkflow(request, originalName);
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    const nameInput = page.locator('input[placeholder="工作流名称"]');
    await expect(nameInput).toHaveValue(originalName);

    await page.getByRole("button", { name: "评论与审阅", exact: true }).click();
    const panel = page.getByRole("dialog", { name: "评论与审阅" });
    await expect(panel).toBeVisible();
    await expect(panel.getByLabel("工作流版本")).toContainText("v1");

    const commentResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workflows/${workflow.id}/comments`),
    );
    await panel.getByPlaceholder("添加评论").fill("Verify the end node output");
    await panel.getByTitle("发送评论").click();
    expect((await commentResponse).status()).toBe(201);
    await expect(panel.getByText("Verify the end node output", { exact: true })).toBeVisible();

    const rootComment = panel.getByRole("listitem").filter({
      hasText: "Verify the end node output",
    });
    await rootComment.getByTitle("回复评论").click();
    const replyResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workflows/${workflow.id}/comments`),
    );
    await panel.getByPlaceholder("回复评论").fill("Output verified");
    await panel.getByTitle("发送回复").click();
    const reply = await replyResponse;
    expect(reply.status()).toBe(201);
    expect(((await reply.json()) as { parent_comment_id: string | null }).parent_comment_id).not.toBeNull();
    await expect(panel.getByText("Output verified", { exact: true })).toBeVisible();

    const reviewResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workflows/${workflow.id}/reviews`),
    );
    await panel.getByPlaceholder("审阅说明").fill("Ready for an independent review");
    await panel.getByRole("button", { name: "发起审阅" }).click();
    const requested = await reviewResponse;
    expect(requested.status()).toBe(201);
    const review = (await requested.json()) as { id: string };
    await expect(panel.getByText("open", { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "通过" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "需修改" })).toHaveCount(0);

    const approved = await request.put(
      `${API_URL}/api/workflows/${workflow.id}/reviews/${review.id}/decision`,
      {
        headers: adminHeaders,
        data: { decision: "approved", summary: "Approved in E2E" },
      },
    );
    expect(approved.status()).toBe(200);
    await panel.getByTitle("刷新").click();
    await expect(panel.getByText("approved", { exact: true })).toBeVisible();
    await rootComment.getByTitle("解决评论").click();
    await expect(rootComment.getByTitle("重新打开评论")).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await panel.getByRole("button", { name: "关闭", exact: true }).click();

    const currentResponse = await request.get(
      `${API_URL}/api/workflows/${workflow.id}`,
      { headers: adminHeaders },
    );
    expect(currentResponse.status()).toBe(200);
    const current = (await currentResponse.json()) as {
      dsl: Record<string, unknown> & {
        nodes: { position: { x: number } }[];
      };
    };
    const remoteDsl = structuredClone(current.dsl);
    remoteDsl.nodes[1].position.x = 360;
    const remote = await request.put(`${API_URL}/api/workflows/${workflow.id}`, {
      headers: adminHeaders,
      data: {
        dsl: remoteDsl,
        version: 1,
        change_summary: "Remote layout change",
      },
    });
    expect(remote.status()).toBe(200);

    const staleSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith(`/api/workflows/${workflow.id}`),
    );
    await nameInput.fill(localName);
    expect((await staleSave).status()).toBe(409);
    const conflict = page.getByRole("alertdialog", { name: "工作流版本冲突" });
    await expect(conflict).toBeVisible();

    const mergeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workflows/${workflow.id}/versions/merge`),
    );
    await conflict.getByRole("button", { name: "自动合并" }).click();
    const merged = await mergeResponse;
    expect(merged.status()).toBe(200);
    expect(((await merged.json()) as { status: string }).status).toBe("merged");
    await expect(conflict).not.toBeVisible();
    await expect(nameInput).toHaveValue(localName);

    const persisted = await request.get(`${API_URL}/api/workflows/${workflow.id}`, {
      headers: adminHeaders,
    });
    expect(persisted.status()).toBe(200);
    const persistedWorkflow = (await persisted.json()) as {
      name: string;
      version: number;
      dsl: { nodes: { position: { x: number } }[] };
    };
    expect(persistedWorkflow.name).toBe(localName);
    expect(persistedWorkflow.version).toBe(3);
    expect(persistedWorkflow.dsl.nodes[1].position.x).toBe(360);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    const expectedConflict = errors.httpErrors.filter(
      (entry) =>
        entry.startsWith("409 ") &&
        entry.endsWith(`/api/workflows/${workflow.id}`),
    );
    expect(expectedConflict).toHaveLength(1);
    expect(
      errors.httpErrors.filter((entry) => !expectedConflict.includes(entry)),
    ).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
