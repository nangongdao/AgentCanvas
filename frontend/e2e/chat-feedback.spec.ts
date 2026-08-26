import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  API_URL,
  TOKENS,
  adminHeaders,
  captureUnexpectedErrors,
  chatWorkflowBody,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
  workflowBody,
} from "./support";

const PASSWORD = "BrowserPass!2026";

test("chat deepening: feedback, promote, regenerate, variables, export", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const workflowName = `Chat Feedback E2E ${suffix}`;
  const created = await createWorkflowFromBody(
    request,
    chatWorkflowBody(workflowName),
  );
  let bodyCompleted = false;

  try {
    await page.goto("/chat");
    await login(page, "editor");

    // Start a session on the fresh workflow.
    await page.getByRole("button", { name: "新会话" }).click();
    await page.getByRole("button", { name: new RegExp(workflowName) }).click();
    const input = page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行");
    await expect(input).toBeVisible();
    await input.fill("e2e feedback question");
    await input.press("Enter");

    // The mock provider streams a deterministic reply.
    await expect(page.getByText("[Mock 模式]", { exact: false })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator("textarea")).toBeEnabled();

    // Negative feedback highlights 👎 and reveals the promote action.
    const thumbsDown = page.getByTitle("无帮助");
    await expect(thumbsDown).toBeVisible();
    await thumbsDown.click();
    await expect(thumbsDown).toHaveClass(/bg-bad\/20/);
    const promote = page.getByTitle("把该轮加入评测数据集");
    await expect(promote).toBeVisible();

    // Promote into a fresh evaluation dataset (dialog prompt for the name).
    const datasetName = `E2E 反馈集 ${suffix}`;
    page.once("dialog", (dialog) => void dialog.accept(datasetName));
    await promote.click();
    await expect(page.getByText("已加入评测数据集", { exact: false })).toBeVisible();

    const datasets = await request.get(
      `${API_URL}/api/evaluation-datasets?search=${encodeURIComponent(datasetName)}`,
      { headers: adminHeaders },
    );
    expect(datasets.status()).toBe(200);
    const items = ((await datasets.json()) as { items: Array<{ name: string }> }).items;
    expect(items.some((d) => d.name === datasetName)).toBe(true);

    // Regenerate replaces the reply with a new assistant turn.
    await page.getByTitle("重新生成回复").click();
    await expect(page.locator("textarea")).toBeEnabled({ timeout: 20_000 });
    await expect(page.getByText("[Mock 模式]", { exact: false })).toBeVisible({
      timeout: 20_000,
    });

    // Session variables panel: add a variable and see it persisted.
    await page.getByTitle("会话变量").click();
    await page.getByPlaceholder("变量名（如 summary）").fill("topic");
    await page.getByPlaceholder('JSON 值（如 "要点…" 或 42）').fill('"e2e-topic"');
    await page.getByRole("button", { name: "添加变量" }).click();
    await expect(
      page.locator("aside[aria-label='会话变量'] span.font-mono", { hasText: "topic" }),
    ).toBeVisible();

    // Export affordances point at the session export endpoint.
    await expect(page.getByTitle("导出 Markdown")).toHaveAttribute(
      "href",
      /\/api\/chat\/sessions\/.+\/export\?format=markdown/,
    );
    await expect(page.getByTitle("导出 JSON")).toHaveAttribute(
      "href",
      /\/api\/chat\/sessions\/.+\/export\?format=json/,
    );

    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, created.id, bodyCompleted);
  }

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("runtime end users can leave feedback on a public app reply", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const ownerEmail = `feedback-owner-${suffix}@example.test`;

  const registered = await request.post(`${API_URL}/api/auth/register`, {
    headers: { Authorization: `Bearer ${TOKENS.admin}` },
    data: {
      email: ownerEmail,
      password: PASSWORD,
      display_name: "Feedback Owner",
      role: "admin",
    },
  });
  expect(registered.status()).toBe(201);
  const loginResponse = await request.post(`${API_URL}/api/auth/login`, {
    data: { email: ownerEmail, password: PASSWORD },
  });
  expect(loginResponse.status()).toBe(200);

  const organization = await request.post(`${API_URL}/api/organizations`, {
    data: { name: `Feedback Org ${suffix}` },
  });
  const organizationId = ((await organization.json()) as { id: string }).id;
  const project = await request.post(
    `${API_URL}/api/organizations/${organizationId}/projects`,
    { data: { name: `Feedback Project ${suffix}` } },
  );
  const projectId = ((await project.json()) as { id: string }).id;

  const workflow = await request.post(`${API_URL}/api/workflows`, {
    data: { ...workflowBody(`Feedback Runtime ${suffix}`), project_id: projectId },
  });
  expect(workflow.status()).toBe(201);
  const workflowId = ((await workflow.json()) as { id: string }).id;
  const publishResponse = await request.post(
    `${API_URL}/api/workflows/${workflowId}/publish`,
  );
  expect(publishResponse.status()).toBe(200);
  const versions = await request.get(`${API_URL}/api/workflows/${workflowId}/versions`);
  const versionId = (
    (await versions.json()) as Array<{ id: string; status: string }>
  ).find((row) => row.status === "published")?.id;
  expect(versionId).toBeTruthy();

  const created = await request.post(`${API_URL}/api/apps`, {
    data: {
      project_id: projectId,
      workflow_id: workflowId,
      name: "Feedback Runtime App",
      visibility: "public",
      welcome_message: "Welcome to feedback runtime",
    },
  });
  expect(created.status()).toBe(201);
  const appBody = (await created.json()) as { app: { id: string; slug: string } };
  const bound = await request.post(`${API_URL}/api/apps/${appBody.app.id}/version`, {
    data: { version_id: versionId },
  });
  expect(bound.status()).toBe(200);

  // The standalone runtime needs no platform session.
  await page.goto(`/apps/p/${appBody.app.slug}`);
  await expect(page.getByText("Welcome to feedback runtime")).toBeVisible();
  const input = page.getByPlaceholder("输入消息，Enter 发送");
  await input.fill("runtime feedback turn");
  await input.press("Enter");
  await expect(page.getByText("runtime feedback turn").last()).toBeVisible({
    timeout: 20_000,
  });
  await expect(input).toBeEnabled({ timeout: 20_000 });

  // 👎 persists through the public feedback endpoint.
  const thumbsDown = page.getByTitle("这个回答没有帮助");
  await expect(thumbsDown).toBeVisible();
  await thumbsDown.click();
  await expect(thumbsDown).toHaveClass(/bg-bad\/20/);

  // The durable row is visible to the platform editor side.
  const sessions = await request.get(
    `${API_URL}/api/chat/sessions?workflow_id=${workflowId}`,
    { headers: adminHeaders },
  );
  expect(sessions.status()).toBe(200);
  const sessionRows = (
    (await sessions.json()) as { items: Array<{ id: string; app_id: string | null }> }
  ).items;
  const runtimeSession = sessionRows.find((row) => row.app_id === appBody.app.id);
  expect(runtimeSession).toBeTruthy();
  const messages = await request.get(
    `${API_URL}/api/chat/sessions/${runtimeSession!.id}/messages`,
    { headers: adminHeaders },
  );
  const assistant = (
    (await messages.json()) as { items: Array<{ id: string; role: string }> }
  ).items.find((m) => m.role === "assistant");
  expect(assistant).toBeTruthy();
  const feedback = await request.get(
    `${API_URL}/api/chat/sessions/${runtimeSession!.id}/messages/${assistant!.id}/feedback`,
    { headers: adminHeaders },
  );
  expect(feedback.status()).toBe(200);
  expect(((await feedback.json()) as { rating: string }).rating).toBe("negative");

  // C3-5: the platform usage view attributes the runtime activity to the app.
  await page.goto(`/apps?project_id=${projectId}`);
  await login(page, "admin");
  const appRow = page.locator("li", { hasText: "Feedback Runtime App" });
  await expect(appRow).toBeVisible();

  // C5-11: the apps list surface passes serious axe checks.
  const appsAxe = await new AxeBuilder({ page }).analyze();
  expect(
    appsAxe.violations.filter((v) =>
      ["critical", "serious"].includes(v.impact ?? ""),
    ),
  ).toEqual([]);
  await appRow.getByTitle("应用用量").click();
  const usageDialog = page.locator("div.fixed.inset-0.z-50", { hasText: "应用用量" });
  await expect(usageDialog).toBeVisible();
  await expect(usageDialog.getByText("会话数")).toBeVisible();
  // At least one session (the runtime bootstrap may create an extra empty one
  // under React StrictMode in the dev server).
  await expect(
    usageDialog
      .locator("div.rounded-md.border.border-line.bg-void\\/60", {
        hasText: "会话数",
      })
      .locator("div.font-semibold"),
  ).toHaveText(/[1-9]\d*/);
  await expect(usageDialog.getByText(/消息数/)).toBeVisible();
  await expect(usageDialog.getByText(/0.0%|暂无反馈/)).toBeVisible();
  await expect(usageDialog.getByText(/每日趋势/)).toBeVisible();

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
