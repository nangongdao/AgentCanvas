import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  logout,
  workflowBody,
} from "./support";

const PASSWORD = "BrowserPass!2026";

async function loginAccount(page: Page, email: string) {
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(PASSWORD);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
}

test("project quotas configure limits and enforce concurrency, storage, and membership", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const ownerEmail = `quota-owner-${suffix}@example.test`;
  const viewerEmail = `quota-viewer-${suffix}@example.test`;

  for (const [email, role, displayName] of [
    [ownerEmail, "admin", "Quota Owner"],
    [viewerEmail, "viewer", "Quota Viewer"],
  ] as const) {
    const registered = await request.post(`${API_URL}/api/auth/register`, {
      headers: adminHeaders,
      data: {
        email,
        password: PASSWORD,
        display_name: displayName,
        role,
      },
    });
    expect(registered.status()).toBe(201);
  }

  const ownerLogin = await request.post(`${API_URL}/api/auth/login`, {
    data: { email: ownerEmail, password: PASSWORD },
  });
  expect(ownerLogin.status()).toBe(200);
  const organization = await request.post(`${API_URL}/api/organizations`, {
    data: { name: `Quota Browser ${suffix}` },
  });
  expect(organization.status()).toBe(201);
  const organizationId = ((await organization.json()) as { id: string }).id;
  const membership = await request.post(
    `${API_URL}/api/organizations/${organizationId}/members`,
    { data: { email: viewerEmail, role: "viewer" } },
  );
  expect(membership.status()).toBe(201);
  const projectResponse = await request.post(
    `${API_URL}/api/organizations/${organizationId}/projects`,
    { data: { name: `Quota Project ${suffix}` } },
  );
  expect(projectResponse.status()).toBe(201);
  const projectId = ((await projectResponse.json()) as { id: string }).id;

  const workflowResponse = await request.post(`${API_URL}/api/workflows`, {
    data: {
      ...workflowBody(`Quota Workflow ${suffix}`),
      project_id: projectId,
    },
  });
  expect(workflowResponse.status()).toBe(201);
  const workflowId = ((await workflowResponse.json()) as { id: string }).id;
  const knowledgeResponse = await request.post(`${API_URL}/api/knowledge-bases`, {
    data: { name: `Quota KB ${suffix}`, project_id: projectId },
  });
  expect(knowledgeResponse.status()).toBe(201);
  const knowledgeId = ((await knowledgeResponse.json()) as { id: string }).id;

  await page.goto(`/quotas?project_id=${projectId}`);
  await loginAccount(page, ownerEmail);
  await expect(page.getByRole("heading", { name: "项目配额", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择项目")).toHaveValue(projectId);
  await expect(page.getByText("可配置", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("并发执行无限").uncheck();
  await page.getByLabel("文档存储无限").uncheck();
  await page.getByLabel("并发执行上限").fill("0");
  await page.getByLabel("文档存储字节上限").fill("4");
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      response.url().endsWith(`/api/projects/${projectId}/quotas`),
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByText("项目配额已保存", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeVisible();

  const deniedRun = await page.request.post(
    `${API_URL}/api/workflows/${workflowId}/run`,
    { data: { inputs: { user_query: "quota" } } },
  );
  expect(deniedRun.status()).toBe(429);
  const deniedUpload = await page.request.post(
    `${API_URL}/api/knowledge-bases/${knowledgeId}/documents`,
    {
      multipart: {
        file: {
          name: "over-limit.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("12345"),
        },
      },
    },
  );
  expect(deniedUpload.status()).toBe(429);

  await logout(page);
  await loginAccount(page, viewerEmail);
  await expect(page).toHaveURL(
    new RegExp(`/settings/quotas\\?project_id=${projectId}$`),
  );
  await expect(page.getByText("只读", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
  const deniedUpdate = await page.request.put(
    `${API_URL}/api/projects/${projectId}/quotas`,
    { data: { storage_bytes_limit: 8 } },
  );
  expect(deniedUpdate.status()).toBe(403);

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(
    errors.httpErrors.filter(
      (entry) =>
        !entry.startsWith("429 ") &&
        !entry.startsWith("403 "),
    ),
  ).toEqual([]);
});
