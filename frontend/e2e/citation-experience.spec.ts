import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  API_URL,
  TOKENS,
  captureUnexpectedErrors,
  login,
  logout,
} from "./support";

const PASSWORD = "BrowserPass!2026";

function citationWorkflowBody(name: string, projectId: string | null, kbId: string) {
  return {
    name,
    ...(projectId ? { project_id: projectId } : {}),
    dsl: {
      version: "1.0",
      name,
      variables: [{ name: "user_query", type: "string", required: true }],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        {
          id: "start",
          type: "start",
          position: { x: 0, y: 100 },
          config: {
            input_schema: [{ name: "user_query", type: "string", required: true }],
          },
        },
        {
          id: "retrieve",
          type: "rag",
          position: { x: 260, y: 100 },
          config: {
            kb_id: kbId,
            query: "{{input.user_query}}",
            top_k: 1,
            score_threshold: 0,
            output_format: "merged_text",
          },
        },
        {
          id: "end",
          type: "end",
          position: { x: 520, y: 100 },
          config: { output_template: { answer: "{{nodes.retrieve.text}}" } },
        },
      ],
      edges: [
        { id: "start-retrieve", source: "start", target: "retrieve" },
        { id: "retrieve-end", source: "retrieve", target: "end" },
      ],
    },
  };
}

async function waitForIngestion(
  request: APIRequestContext,
  kbId: string,
  documentId: string,
  headers?: Record<string, string>,
) {
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `${API_URL}/api/knowledge-bases/${kbId}/documents/${documentId}/ingest`,
          { headers },
        );
        expect(response.status()).toBe(200);
        const body = (await response.json()) as { job_status: string | null };
        return body.job_status;
      },
      { timeout: 30_000 },
    )
    .toBe("succeeded");
}

test("citations expand context, open sources, focus documents, and report coverage", async ({
  page,
  request,
}) => {
  test.setTimeout(150_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const ownerEmail = `citation-owner-${suffix}@example.test`;
  const workflowName = `Citation Experience ${suffix}`;
  const runtimeWorkflowName = `${workflowName} Runtime`;
  const filename = `citation-handbook-${suffix}.md`;

  const registered = await request.post(`${API_URL}/api/auth/register`, {
    headers: { Authorization: `Bearer ${TOKENS.admin}` },
    data: {
      email: ownerEmail,
      password: PASSWORD,
      display_name: "Citation Owner",
      role: "admin",
    },
  });
  expect(registered.status()).toBe(201);
  const loggedIn = await request.post(`${API_URL}/api/auth/login`, {
    data: { email: ownerEmail, password: PASSWORD },
  });
  expect(loggedIn.status()).toBe(200);

  const organization = await request.post(`${API_URL}/api/organizations`, {
    data: { name: `Citation Org ${suffix}` },
  });
  expect(organization.status()).toBe(201);
  const organizationId = ((await organization.json()) as { id: string }).id;
  const project = await request.post(
    `${API_URL}/api/organizations/${organizationId}/projects`,
    { data: { name: `Citation Project ${suffix}` } },
  );
  expect(project.status()).toBe(201);
  const projectId = ((await project.json()) as { id: string }).id;

  const kb = await request.post(`${API_URL}/api/knowledge-bases`, {
    data: {
      project_id: projectId,
      name: `Citation KB ${suffix}`,
      chunk_size: 128,
      chunk_overlap: 16,
      split_strategy: "heading",
      parent_chunk: true,
    },
  });
  expect(kb.status()).toBe(201);
  const kbId = ((await kb.json()) as { id: string }).id;
  const documentText = [
    "# Citation Operations",
    "Meridian relay protocol is the primary evidence used for citation retrieval.",
    "Operators validate source identity, review the captured segment, and preserve the audit trail before release.",
    "The runbook keeps neighboring instructions visible so a narrow retrieval result never loses its operational meaning.",
    "Expanded parent context proves that the surrounding heading section remains available to readers.",
  ].join("\n\n");
  const uploaded = await request.post(
    `${API_URL}/api/knowledge-bases/${kbId}/documents`,
    {
      multipart: {
        file: {
          name: filename,
          mimeType: "text/markdown",
          buffer: Buffer.from(documentText),
        },
      },
    },
  );
  expect(uploaded.status()).toBe(201);
  const documentId = ((await uploaded.json()) as { id: string }).id;
  const ingest = await request.post(
    `${API_URL}/api/knowledge-bases/${kbId}/documents/${documentId}/ingest`,
  );
  expect(ingest.status()).toBe(202);
  await waitForIngestion(request, kbId, documentId);

  const platformKb = await request.post(`${API_URL}/api/knowledge-bases`, {
    headers: { Authorization: `Bearer ${TOKENS.admin}` },
    data: {
      name: `Citation Platform KB ${suffix}`,
      chunk_size: 128,
      chunk_overlap: 16,
      split_strategy: "heading",
      parent_chunk: true,
    },
  });
  expect(platformKb.status()).toBe(201);
  const platformKbId = ((await platformKb.json()) as { id: string }).id;
  const platformUpload = await request.post(
    `${API_URL}/api/knowledge-bases/${platformKbId}/documents`,
    {
      headers: { Authorization: `Bearer ${TOKENS.admin}` },
      multipart: {
        file: {
          name: filename,
          mimeType: "text/markdown",
          buffer: Buffer.from(documentText),
        },
      },
    },
  );
  expect(platformUpload.status()).toBe(201);
  const platformDocumentId = ((await platformUpload.json()) as { id: string }).id;
  const platformIngest = await request.post(
    `${API_URL}/api/knowledge-bases/${platformKbId}/documents/${platformDocumentId}/ingest`,
    { headers: { Authorization: `Bearer ${TOKENS.admin}` } },
  );
  expect(platformIngest.status()).toBe(202);
  await waitForIngestion(request, platformKbId, platformDocumentId, {
    Authorization: `Bearer ${TOKENS.admin}`,
  });

  const workflow = await request.post(`${API_URL}/api/workflows`, {
    data: citationWorkflowBody(runtimeWorkflowName, projectId, kbId),
  });
  expect(workflow.status()).toBe(201);
  const workflowId = ((await workflow.json()) as { id: string }).id;
  const published = await request.post(
    `${API_URL}/api/workflows/${workflowId}/publish`,
  );
  expect(published.status()).toBe(200);
  const versionId = ((await published.json()) as { id: string }).id;
  const createdApp = await request.post(`${API_URL}/api/apps`, {
    data: {
      project_id: projectId,
      workflow_id: workflowId,
      name: `Citation App ${suffix}`,
      visibility: "public",
    },
  });
  expect(createdApp.status()).toBe(201);
  const app = (await createdApp.json()) as {
    app: { id: string; slug: string };
  };
  const bound = await request.post(`${API_URL}/api/apps/${app.app.id}/version`, {
    data: { version_id: versionId },
  });
  expect(bound.status()).toBe(200);

  const createdCompletion = await request.post(`${API_URL}/api/apps`, {
    data: {
      project_id: projectId,
      workflow_id: workflowId,
      name: `Citation Completion ${suffix}`,
      type: "completion",
      visibility: "public",
    },
  });
  expect(createdCompletion.status()).toBe(201);
  const completionApp = (await createdCompletion.json()) as {
    app: { id: string; slug: string };
  };
  const completionBound = await request.post(
    `${API_URL}/api/apps/${completionApp.app.id}/version`,
    { data: { version_id: versionId } },
  );
  expect(completionBound.status()).toBe(200);

  const platformWorkflow = await request.post(`${API_URL}/api/workflows`, {
    headers: { Authorization: `Bearer ${TOKENS.admin}` },
    data: citationWorkflowBody(workflowName, null, platformKbId),
  });
  expect(platformWorkflow.status()).toBe(201);

  await page.goto("/chat");
  await login(page, "editor");
  await page.getByRole("button", { name: "新会话" }).click();
  await page.getByRole("button", { name: new RegExp(workflowName) }).click();
  const platformInput = page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行");
  await platformInput.fill("meridian relay protocol");
  await platformInput.press("Enter");
  await expect(platformInput).toBeEnabled({ timeout: 30_000 });

  const platformCitation = page.getByRole("button", { name: "展开引用 [1]" });
  await expect(platformCitation).toBeVisible();
  await expect(
    page.getByText("# Citation Operations", { exact: false }),
  ).toBeVisible();
  await platformCitation.click();
  await expect(
    page.getByText("Expanded parent context proves", { exact: false }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: test.info().outputPath("platform-citation-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({
    path: test.info().outputPath("platform-citation-desktop.png"),
    fullPage: true,
  });
  const platformSource = page.getByRole("link", { name: `查看原文 ${filename}` });
  await expect(platformSource).toHaveAttribute(
    "href",
    `/knowledge?kb_id=${platformKbId}&document_id=${platformDocumentId}`,
  );
  let targetedDocumentRequests = 0;
  page.on("request", (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (
      browserRequest.method() === "GET" &&
      url.pathname ===
        `/api/knowledge-bases/${platformKbId}/documents/${platformDocumentId}`
    ) {
      targetedDocumentRequests += 1;
    }
  });
  await page.route("**/api/knowledge-bases/**", async (route) => {
    const url = new URL(route.request().url());
    if (
      route.request().method() !== "GET" ||
      url.pathname !== `/api/knowledge-bases/${platformKbId}/documents`
    ) {
      await route.fallback();
      return;
    }
    const response = await route.fetch();
    const pageBody = (await response.json()) as {
      items: Array<{ id: string }>;
      [key: string]: unknown;
    };
    await route.fulfill({
      response,
      json: {
        ...pageBody,
        items: pageBody.items.filter((document) => document.id !== platformDocumentId),
        has_more: true,
      },
    });
  });
  await platformSource.click();
  await expect(page).toHaveURL(
    new RegExp(
      `/knowledge\\?kb_id=${platformKbId}&document_id=${platformDocumentId}$`,
    ),
  );
  await expect(
    page.locator(`[data-document-id="${platformDocumentId}"]`),
  ).toHaveAttribute("data-focused", "true");
  await expect(page.getByText(filename, { exact: true })).toBeVisible();
  expect(targetedDocumentRequests).toBeGreaterThanOrEqual(1);
  await page.unroute("**/api/knowledge-bases/**");

  await page.goto(`/apps/p/${app.app.slug}`);
  const runtimeInput = page.getByPlaceholder("输入消息，Enter 发送");
  await runtimeInput.fill("meridian relay protocol");
  await runtimeInput.press("Enter");
  await expect(runtimeInput).toBeEnabled({ timeout: 30_000 });
  const runtimeCitation = page.getByRole("button", { name: "展开引用 [1]" });
  await expect(runtimeCitation).toBeVisible();
  await expect(
    page.getByText("# Citation Operations", { exact: false }).first(),
  ).toBeVisible();
  await runtimeCitation.click();
  await expect(
    page.getByText("Expanded parent context proves", { exact: false }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: test.info().outputPath("runtime-citation-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({
    path: test.info().outputPath("runtime-citation-desktop.png"),
    fullPage: true,
  });
  const runtimeSource = page.getByRole("link", { name: `查看原文 ${filename}` });
  const runtimeHref = await runtimeSource.getAttribute("href");
  expect(runtimeHref).toMatch(
    new RegExp(
      `/api/apps/p/${app.app.slug}/sessions/.+/messages/.+/citations/.+/source$`,
    ),
  );
  const sourceResponse = await request.get(`${API_URL}${runtimeHref}`);
  expect(sourceResponse.status()).toBe(200);
  expect(await sourceResponse.text()).toBe(documentText);

  await page.goto(`/apps/p/${completionApp.app.slug}`);
  const completionInput = page.getByPlaceholder("输入 user_query");
  await completionInput.fill("meridian relay protocol");
  await page.getByRole("button", { name: "提交" }).click();
  await expect(page.getByRole("button", { name: "提交" })).toBeEnabled({
    timeout: 30_000,
  });
  const completionCitation = page.getByRole("button", { name: "展开引用 [1]" });
  await expect(completionCitation).toBeVisible();
  await expect(
    page.getByText("Meridian relay protocol is the primary evidence", {
      exact: false,
    }),
  ).toBeVisible();
  await completionCitation.click();
  await expect(
    page.getByText("Expanded parent context proves", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: `查看原文 ${filename}` })).toHaveAttribute(
    "href",
    new RegExp(
      `/api/apps/p/${completionApp.app.slug}/sessions/.+/messages/.+/citations/.+/source$`,
    ),
  );

  await page.goto(`/apps?project_id=${projectId}`);
  await logout(page);
  await login(page, "admin");
  const appRow = page.locator("li", { hasText: `Citation App ${suffix}` });
  await appRow.getByTitle("应用用量").click();
  const usageDialog = page.locator("div.fixed.inset-0.z-50", { hasText: "应用用量" });
  await expect(usageDialog.getByText("引用覆盖率", { exact: true })).toBeVisible();
  await expect(usageDialog.getByText("100.0%", { exact: true })).toBeVisible();
  await expect(usageDialog.getByText("1 / 1", { exact: true })).toBeVisible();

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
