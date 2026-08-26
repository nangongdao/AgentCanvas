import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  chatWorkflowBody,
  cleanupWorkflow,
  createWorkflow,
  createWorkflowFromBody,
  editorHeaders,
  humanWorkflowBody,
  login,
} from "./support";

test("human approval pauses and resumes through the execution drawer", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const name = `Human E2E ${Date.now()}`;
  const workflow = await createWorkflowFromBody(request, humanWorkflowBody(name));
  let bodyCompleted = false;
  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    await expect(page.getByText("waiting_approval", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Release approval", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "批准", exact: true }).click();
    await expect(page.getByText("succeeded", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("workflow_finished", { exact: true })).toBeVisible();
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("model management enforces admin CRUD permissions", async ({ page, request }) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const modelId = `e2e-model-${suffix}`;
  let created = false;
  let bodyCompleted = false;
  try {
    const denied = await request.post(`${API_URL}/api/models`, {
      headers: editorHeaders,
      data: {
        id: `${modelId}-denied`,
        name: "Denied model",
        provider: "mock",
        model_name: "mock",
        kind: "chat",
      },
    });
    expect(denied.status()).toBe(403);

    await page.goto("/models");
    await login(page, "admin");
    await page.getByRole("button", { name: "新建模型", exact: true }).click();
    await page.getByLabel("名称").fill(`Model E2E ${suffix}`);
    await page
      .getByRole("dialog")
      .getByRole("combobox", { name: "Provider", exact: true })
      .selectOption("mock");
    await page.getByLabel("模型名 (model_name)").fill("mock");
    const createResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" && response.url().endsWith("/api/models"),
    );
    await page.getByRole("button", { name: "保存", exact: true }).click();
    const response = await createResponse;
    expect(response.status()).toBe(201);
    const payload = (await response.json()) as { id: string };
    expect(payload.id).toBeTruthy();
    created = true;
    await expect(page.getByText(`Model E2E ${suffix}`, { exact: true })).toBeVisible();
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
    if (created) {
      const removed = await request.delete(`${API_URL}/api/models/${payload.id}`, {
        headers: adminHeaders,
      });
      expect(removed.status()).toBe(204);
      created = false;
    }
  } finally {
    if (created) {
      const models = await request.get(`${API_URL}/api/models`, { headers: adminHeaders });
      if (models.ok()) {
        const row = ((await models.json()) as Array<{ id: string; name: string }>).find(
          (item) => item.name === `Model E2E ${suffix}`,
        );
        if (row) await request.delete(`${API_URL}/api/models/${row.id}`, { headers: adminHeaders });
      }
    }
    void bodyCompleted;
  }
});

test("Chat streams, stops, and reloads persisted messages", async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const name = `Chat E2E ${Date.now()}`;
  const workflow = await createWorkflowFromBody(request, chatWorkflowBody(name));
  let sessionId = "";
  let bodyCompleted = false;
  try {
    await page.goto("/chat");
    await login(page, "editor");

    // C5-11: the chat surface (session rail + composer) passes serious axe.
    const chatAxe = await new AxeBuilder({ page }).analyze();
    expect(
      chatAxe.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    await page.getByRole("button", { name: "新会话", exact: true }).click();
    const sessionResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" && response.url().endsWith("/api/chat/sessions"),
    );
    await page.getByRole("button", { name: new RegExp(name) }).click();
    const createdSession = await sessionResponse;
    expect(createdSession.status()).toBe(201);
    sessionId = ((await createdSession.json()) as { id: string }).id;

    const input = page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行");
    await input.fill("persist this chat turn");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText(/\[Mock 模式\].*persist this chat turn/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible({ timeout: 30_000 });

    await page.reload();
    await page.getByRole("button", { name: new RegExp(name) }).click();
    await expect(page.getByText("persist this chat turn", { exact: true })).toBeVisible();
    await expect(page.getByText(/\[Mock 模式\].*persist this chat turn/)).toBeVisible();

    await input.fill("stop this streamed turn");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible();
    const mockReplies = page.getByText(/\[Mock 模式\]/);
    await expect(mockReplies).toHaveCount(2);
    await expect(mockReplies.last()).toContainText("stop this streamed turn");
    const cancelResponse = page.waitForResponse(
      (response) => response.request().method() === "POST" && response.url().endsWith("/cancel"),
    );
    await page.getByRole("button", { name: "停止", exact: true }).click();
    expect((await cancelResponse).status()).toBe(200);
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (sessionId) {
      const removed = await request.delete(`${API_URL}/api/chat/sessions/${sessionId}`, {
        headers: adminHeaders,
      });
      if (bodyCompleted) expect(removed.status()).toBe(204);
    }
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("unavailable Redis is visible as SQLite memory fallback", async ({ page }) => {
  await page.goto("/");
  const meta = await page.evaluate(async () => {
    const response = await fetch("/api/meta");
    return response.json() as Promise<{ memory_backend: string; database: string }>;
  });
  expect(meta.memory_backend).toBe("sqlite");
  expect(meta.database).toBe("sqlite");
});

test("dialogs keep keyboard focus contained and pass serious axe checks", async ({
  page,
  request,
}) => {
  const created = await createWorkflow(request, `Accessibility E2E ${Date.now()}`);
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${created.id}`);
    await login(page, "admin");

    const opener = page.locator('button[title="打开工作流"]');
    await opener.focus();
    await opener.click();

    const dialog = page.getByRole("dialog", { name: "工作流目录" });
    await expect(dialog).toBeVisible();
    await expect(page.locator('input[aria-label="搜索工作流"]')).toBeFocused();
    const activeInsideDialog = async () =>
      page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')));

    await dialog.locator('input[aria-label="搜索工作流"]').focus();
    await page.keyboard.press("Shift+Tab");
    await expect.poll(activeInsideDialog).toBe(true);
    await page.keyboard.press("Tab");
    await expect.poll(activeInsideDialog).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();

    await page.emulateMedia({ reducedMotion: "reduce" });
    await opener.click();
    await expect(page.getByRole("dialog", { name: "工作流目录" })).toBeVisible();
    const axe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    const blocking = axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);

    const motionDurations = await page.locator('[role="dialog"] *').evaluateAll((elements) =>
      elements.map((element) => {
        const style = getComputedStyle(element);
        return { animation: style.animationDuration, transition: style.transitionDuration };
      }),
    );
    expect(
      motionDurations.every(({ animation, transition }) =>
        [animation, transition].every((duration) =>
          duration.split(",").every((part) => {
            const value = part.trim();
            const milliseconds = value.endsWith("ms")
              ? Number.parseFloat(value)
              : value.endsWith("s")
                ? Number.parseFloat(value) * 1000
                : Number.NaN;
            return Number.isFinite(milliseconds) && milliseconds <= 0.01;
          }),
        ),
      ),
      JSON.stringify(motionDurations),
    ).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "工作流目录" })).toHaveCount(0);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, created.id, bodyCompleted);
  }
});
