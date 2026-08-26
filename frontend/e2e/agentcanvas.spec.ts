import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  API_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createRagWorkflow,
  createWorkflow,
  createWorkflowFromBody,
  login,
  logout,
} from "./support";

test("token roles preserve deep links and expose only permitted controls", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const created = await createWorkflow(request, `Auth E2E ${Date.now()}`);
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${created.id}`);
    await expect(page.locator("#auth-dialog-title")).toBeVisible();
    await expect(page.locator('button[title="当前访问角色与偏好"]')).toHaveCount(0);

    await login(page, "viewer");
    await expect(page).toHaveURL(new RegExp(`/workflows/${created.id}$`));
    await expect(page.locator('button[title="保存工作流"]')).toHaveCount(0);
    await expect(page.locator('button[title="管理 MCP 服务"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: "运行", exact: true })).toHaveCount(0);
    await expect(page.locator('input[placeholder="工作流名称"]')).not.toBeEditable();

    await page.reload();
    await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText("viewer");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    const firstNode = page.locator(".react-flow__node").first();
    await expect(firstNode).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    const nodeBox = await firstNode.boundingBox();
    expect(nodeBox).not.toBeNull();
    expect(nodeBox!.width).toBeGreaterThan(88);

    await page.setViewportSize({ width: 1440, height: 900 });
    await logout(page);
    await expect(page).toHaveURL(new RegExp(`/workflows/${created.id}$`));
    await login(page, "editor");
    await expect(page.locator('button[title="保存工作流"]')).toHaveCount(1);
    await expect(page.getByRole("button", { name: "运行", exact: true })).toHaveCount(1);
    await expect(page.locator('button[title="管理 MCP 服务"]')).toHaveCount(0);
    await expect(page.locator('input[placeholder="工作流名称"]')).toBeEditable();

    await logout(page);
    await login(page, "admin");
    await expect(page.locator('button[title="管理 MCP 服务"]')).toHaveCount(1);
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, created.id, bodyCompleted);
  }
});

test("workflow autosave, validated run, and event replay stay operational", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const name = `Workflow E2E ${Date.now()}`;
  let workflowId = "";
  let bodyCompleted = false;

  try {
    await page.goto("/workflows/new");
    await login(page, "admin");
    const nameInput = page.locator('input[placeholder="工作流名称"]');
    await nameInput.fill(name);
    await page.waitForURL(/\/workflows\/[a-f0-9]{32}$/, { timeout: 15_000 });
    workflowId = page.url().split("/").at(-1) ?? "";
    expect(workflowId).toMatch(/^[a-f0-9]{32}$/);
    await expect(page.getByText("synced", { exact: true })).toBeVisible();

    await page.reload();
    await expect(nameInput).toHaveValue(name);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await expect(runDialog).toBeVisible();
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    await expect(runDialog.getByText("此字段为必填项")).toBeVisible();
    await runDialog.locator("input").first().fill("persistent e2e input");

    const startResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workflows/${workflowId}/run`),
    );
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    const response = await startResponse;
    expect(response.status()).toBe(201);
    const execution = (await response.json()) as { id: string };

    await expect
      .poll(
        async () => {
          const current = await request.get(`${API_URL}/api/executions/${execution.id}`, {
            headers: adminHeaders,
          });
          return ((await current.json()) as { status: string }).status;
        },
        { timeout: 20_000 },
    )
      .toBe("succeeded");
    await expect(page.getByText("workflow_finished", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "inspector", exact: true }).click();
    await expect(page.getByText("Node Inspector", { exact: true })).toBeVisible();
    await expect(page.getByText("redacted", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Agent agent/ })).toBeVisible();
    await expect(page.getByText("Input", { exact: true })).toBeVisible();
    await expect(page.getByText("persistent e2e input", { exact: false })).toBeVisible();
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (workflowId) await cleanupWorkflow(request, workflowId, bodyCompleted);
  }
});

test("iteration node configuration persists through workflow autosave", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const name = `Iteration E2E ${Date.now()}`;
  const created = await createWorkflowFromBody(request, {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [{ name: "items", type: "array", required: true }],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        { id: "start", type: "start", position: { x: 0, y: 100 }, config: {} },
        {
          id: "each",
          type: "iteration",
          position: { x: 300, y: 100 },
          config: {
            items: "{{input.items}}",
            batch_size: 10,
            concurrency_limit: 4,
            failure_strategy: "abort",
            subgraph: {
              nodes: [
                { id: "item_start", type: "start", config: {} },
                {
                  id: "item_end",
                  type: "end",
                  config: { output_template: { value: "{{input.item}}" } },
                },
              ],
              edges: [{ id: "item_edge", source: "item_start", target: "item_end" }],
            },
          },
        },
        {
          id: "end",
          type: "end",
          position: { x: 600, y: 100 },
          config: { output_template: { result: "{{nodes.each.output}}" } },
        },
      ],
      edges: [
        { id: "edge_1", source: "start", target: "each" },
        { id: "edge_2", source: "each", target: "end" },
      ],
    },
  });
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${created.id}`);
    await login(page, "admin");
    await expect(page.getByRole("button", { name: /迭代.*数组批处理子流程/ })).toBeVisible();
    await page.locator('.react-flow__node[data-id="each"]').click();

    const editor = page.getByRole("region", { name: "Iteration 配置" });
    await expect(editor).toBeVisible();
    await editor.getByLabel("数组表达式").fill("{{input.items}}");
    await editor.getByLabel("批大小").fill("25");
    await editor.getByLabel("并发上限").fill("5");
    await editor.getByRole("button", { name: "收集", exact: true }).click();
    await expect(editor.getByLabel("子流程 DSL")).toContainText("item_end");

    await expect
      .poll(async () => {
        const response = await request.get(`${API_URL}/api/workflows/${created.id}`, {
          headers: adminHeaders,
        });
        const workflow = (await response.json()) as {
          dsl: { nodes: Array<{ type: string; config: Record<string, unknown> }> };
        };
        const iteration = workflow.dsl.nodes.find((node) => node.type === "iteration");
        return iteration?.config;
      })
      .toMatchObject({
        items: "{{input.items}}",
        batch_size: 25,
        concurrency_limit: 5,
        failure_strategy: "collect_error",
      });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, created.id, bodyCompleted);
  }
});

test("workflow trigger center exposes webhook schedule API and callback controls", async ({
  page,
  request,
}, testInfo) => {
  const errors = captureUnexpectedErrors(page);
  const created = await createWorkflow(request, `Trigger center E2E ${Date.now()}`);
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${created.id}`);
    await login(page, "admin");
    await page.locator('button[title="触发器"]').click();
    const dialog = page.locator('[aria-labelledby="workflow-trigger-title"]');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "触发中心" })).toBeVisible();
    await expect(dialog.getByText("Inbound Webhook", { exact: true })).toBeVisible();

    await dialog.getByRole("tab", { name: "计划", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "新建计划" })).toBeVisible();
    await dialog.getByRole("tab", { name: "API", exact: true }).click();
    await expect(dialog.getByText("Workflow API", { exact: true })).toBeVisible();
    await dialog.getByRole("tab", { name: "回调", exact: true }).click();
    await expect(dialog.getByText("Outbound Callback", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Recent deliveries", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("trigger-center-desktop.png") });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dialog).toBeVisible();
    const overflow = await dialog.evaluate(
      (element) => element.scrollWidth - element.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath("trigger-center-mobile.png") });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(
      errors.httpErrors.filter(
        (entry) =>
          !entry.startsWith("404 ") ||
          !["/webhook", "/api", "/callback", "/callback/deliveries"].some((suffix) =>
            entry.includes(`/api/workflows/${created.id}${suffix}`),
          ),
      ),
    ).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, created.id, bodyCompleted);
  }
});

test("failed node rerun reuses snapshots and switches the live console", async ({
  page,
  request,
}, testInfo) => {
  const errors = captureUnexpectedErrors(page);
  const name = `Rerun E2E ${Date.now()}`;
  const workflow = await createWorkflowFromBody(request, {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [{ name: "user_query", type: "string", required: true }],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        {
          id: "start",
          type: "start",
          name: "Start",
          position: { x: 0, y: 100 },
          config: {
            input_schema: [{ name: "user_query", type: "string", required: true }],
          },
        },
        {
          id: "rag",
          type: "rag",
          name: "Retrieve failure",
          position: { x: 300, y: 100 },
          config: { kb_id: "", query: "{{input.user_query}}" },
        },
        {
          id: "end",
          type: "end",
          name: "End",
          position: { x: 600, y: 100 },
          config: {},
        },
      ],
      edges: [
        { id: "start-rag", source: "start", target: "rag" },
        { id: "rag-end", source: "rag", target: "end" },
      ],
    },
  });
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const dialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await dialog.locator("input").first().fill("replay this retrieval");
    await dialog.getByRole("button", { name: "开始运行" }).click();
    await expect(page.getByText("workflow_failed", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("tab", { name: "inspector", exact: true }).click();
    await expect(page.getByRole("button", { name: /Retrieve failure/ })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await expect(page.getByRole("button", { name: "重跑", exact: true })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("rerun-mobile.png"),
      fullPage: true,
    });

    const rerunResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/executions\/[a-f0-9]{32}\/rerun$/.test(response.url()),
    );
    await page.getByRole("button", { name: "重跑", exact: true }).click();
    const rerun = await rerunResponse;
    expect(rerun.status()).toBe(201);
    const execution = (await rerun.json()) as {
      id: string;
      parent_execution_id: string;
      rerun_from_node_id: string;
    };
    expect(execution.parent_execution_id).toMatch(/^[a-f0-9]{32}$/);
    expect(execution.rerun_from_node_id).toBe("rag");

    await expect(page.getByText("workflow_started", { exact: true })).toBeVisible();
    await expect(page.getByText("workflow_failed", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "inspector", exact: true }).click();
    await expect(page.getByRole("button", { name: /Retrieve failure/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Start start/ })).toHaveCount(0);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({
      path: testInfo.outputPath("rerun-desktop.png"),
      fullPage: true,
    });
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("admin can discover tools from the real calculator MCP server", async ({ page }) => {
  const errors = captureUnexpectedErrors(page);
  await page.goto("/workflows/new");
  await login(page, "admin");
  await page.locator('button[title="管理 MCP 服务"]').click();
  await expect(page.locator("#mcp-manager-title")).toBeVisible();
  await expect(page.getByText("Calculator", { exact: true }).first()).toBeVisible();
  const catalog = page.getByRole("region", { name: "MCP Catalog" });
  await expect(catalog).toBeVisible();
  await expect(catalog.getByRole("combobox", { name: "Catalog version" })).toHaveValue(
    "builtin-calculator-v1",
  );
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  // The backend MCP operation boundary is 30 seconds; the UI assertion must
  // not abandon a still-valid bounded request before that contract expires.
  await expect(page.getByText(/健康检查通过，发现 2 个工具，熔断状态 closed/)).toBeVisible({
    timeout: 35_000,
  });
  await expect(page.getByText("eval_expr", { exact: true })).toBeVisible();
  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});

test("versioned dataset runs a published workflow and exposes an auditable report", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(75_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const name = `Evaluation E2E ${suffix}`;
  const workflow = await createWorkflowFromBody(request, {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [{ name: "question", type: "string", required: true }],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        { id: "start", type: "start", name: "Start", position: { x: 0, y: 100 }, config: {} },
        {
          id: "end",
          type: "end",
          name: "End",
          position: { x: 300, y: 100 },
          config: { output_template: { answer: "{{input.question}}" } },
        },
      ],
      edges: [{ id: "start-end", source: "start", target: "end" }],
    },
  });
  let bodyCompleted = false;

  try {
    const published = await request.post(`${API_URL}/api/workflows/${workflow.id}/publish`, {
      headers: adminHeaders,
    });
    expect(published.status()).toBe(200);
    const versionA = await published.json();
    const currentResponse = await request.get(`${API_URL}/api/workflows/${workflow.id}`, {
      headers: adminHeaders,
    });
    expect(currentResponse.status()).toBe(200);
    const currentWorkflow = await currentResponse.json();
    const changedDsl = structuredClone(currentWorkflow.dsl);
    changedDsl.nodes[1].config = { output_template: { changed: "{{input.question}}" } };
    const changed = await request.put(`${API_URL}/api/workflows/${workflow.id}`, {
      headers: adminHeaders,
      data: { dsl: changedDsl, version: currentWorkflow.version },
    });
    expect(changed.status()).toBe(200);
    const publishedB = await request.post(`${API_URL}/api/workflows/${workflow.id}/publish`, {
      headers: adminHeaders,
    });
    expect(publishedB.status()).toBe(200);
    const versionB = await publishedB.json();

    await page.goto("/evaluations");
    await login(page, "admin");
    await page.getByRole("toolbar", { name: "评测页面操作" }).getByRole("button", { name: "新建数据集", exact: true }).click();
    const datasetDialog = page.locator('[aria-labelledby="dataset-dialog-title"]');
    await datasetDialog.getByLabel("名称", { exact: true }).fill(`Dataset ${suffix}`);
    await datasetDialog.getByLabel("样本 1 ID").fill("browser-case");
    await datasetDialog.getByLabel("样本 1 名称").fill("Browser exact match");
    await datasetDialog.getByLabel("Inputs JSON").fill('{"question":"browser-evaluation"}');
    await datasetDialog.getByLabel("Expected JSON").fill('{"answer":"browser-evaluation"}');
    const datasetResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/evaluation-datasets"),
    );
    await datasetDialog.getByRole("button", { name: "保存版本", exact: true }).click();
    const createdDatasetResponse = await datasetResponse;
    expect(createdDatasetResponse.status()).toBe(201);
    const datasetName = `Dataset ${suffix}`;
    await page
      .getByRole("complementary")
      .first()
      .getByRole("button", { name: new RegExp(`^${datasetName}`) })
      .click();
    await expect(page.getByRole("heading", { name: datasetName, exact: true })).toBeVisible();
    await expect(
      page.getByRole("main").getByText("Browser exact match", { exact: true }),
    ).toBeVisible();

    await page.getByRole("button", { name: "运行评测", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="evaluation-run-dialog-title"]');
    await runDialog.getByLabel("工作流").selectOption({ label: name });
    await expect(runDialog.getByLabel("已发布版本").locator("option")).toHaveCount(3);
    await runDialog.getByLabel("已发布版本").selectOption(versionA.id);
    await expect(runDialog.getByLabel("已发布版本")).toHaveValue(versionA.id);
    const runResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/evaluation-runs"),
    );
    await runDialog.getByRole("button", { name: "开始评测", exact: true }).click();
    expect((await runResponse).status()).toBe(201);
    await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("100%", { exact: true })).toBeVisible();
    await expect(page.getByText("exact match", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "A/B 对比", exact: true }).click();
    const comparisonDialog = page.locator('[aria-labelledby="evaluation-run-dialog-title"]');
    await comparisonDialog.getByLabel("工作流").selectOption({ label: name });
    await expect(comparisonDialog.getByLabel("已发布版本").locator("option")).toHaveCount(3);
    await comparisonDialog.getByLabel("已发布版本").selectOption(versionA.id);
    await comparisonDialog.getByLabel("对比版本 B").selectOption(versionB.id);
    await expect(comparisonDialog.getByLabel("已发布版本")).toHaveValue(versionA.id);
    await expect(comparisonDialog.getByLabel("对比版本 B")).toHaveValue(versionB.id);
    const comparisonResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/evaluation-comparisons"),
    );
    await comparisonDialog.getByRole("button", { name: "开始对比", exact: true }).click();
    expect((await comparisonResponse).status()).toBe(201);
    await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Quality", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", {
        name: `v1 vs v2 ${datasetName} v1`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("-100%", { exact: true })).toBeVisible();
    await expect(page.getByText(/exec [a-f0-9]{32}/)).toHaveCount(2);

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath("evaluations-mobile.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: testInfo.outputPath("evaluations-desktop.png"), fullPage: true });
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("knowledge ingestion, retrieval, RAG execution, and citations stay connected", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const datasetName = `RAG Dataset ${suffix}`;
  let knowledgeBaseId = "";
  let documentId = "";
  let workflowId = "";
  let knowledgeDeleted = false;
  let bodyCompleted = false;

  try {
    await page.goto("/knowledge");
    await login(page, "admin");
    await page.getByRole("toolbar", { name: "知识库页面操作" }).getByRole("button", { name: "新建知识库", exact: true }).click();
    const dialog = page.locator('[aria-labelledby="knowledge-dialog-title"]');
    await dialog.getByLabel("名称").fill(`Knowledge E2E ${suffix}`);
    await dialog.getByLabel("描述").fill("Persistent browser retrieval corpus");
    await dialog.getByLabel("Chunk size").fill("256");
    await dialog.getByLabel("Overlap").fill("32");
    const createResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" && response.url().endsWith("/api/knowledge-bases"),
    );
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    const created = await createResponse;
    expect(created.status()).toBe(201);
    knowledgeBaseId = ((await created.json()) as { id: string }).id;

    const hybridResponse = await request.put(
      `${API_URL}/api/knowledge-bases/${knowledgeBaseId}`,
      {
        headers: adminHeaders,
        data: { retrieval_mode: "hybrid" },
      },
    );
    expect(hybridResponse.status()).toBe(200);

    const uploadResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}/documents`),
    );
    const ingestResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes(`/api/knowledge-bases/${knowledgeBaseId}/documents/`) &&
        response.url().endsWith("/ingest"),
      { timeout: 30_000 },
    );
    await page.locator('input[type="file"]').setInputFiles({
      name: "e2e-handbook.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "AgentCanvas deployment uses a canary rollout with health checks before traffic promotion.",
      ),
    });
    const uploaded = await uploadResponse;
    expect(uploaded.status()).toBe(201);
    documentId = ((await uploaded.json()) as { id: string }).id;
    expect((await ingestResponse).status()).toBe(202);
    await expect(page.getByText("e2e-handbook.md", { exact: true })).toBeVisible();
    await expect(page.getByText("ready", { exact: true })).toBeVisible();

    await page.getByPlaceholder("输入检索查询").fill("canary rollout health checks");
    await page.route(
      `**/api/knowledge-bases/${knowledgeBaseId}/retrieve`,
      async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as {
          rerank_applied: boolean;
          score_detail?: Array<{
            vector_score?: number | null;
            keyword_score?: number | null;
            fused_score?: number | null;
            rerank_score?: number | null;
            rerank_applied: boolean;
          }> | null;
        };
        const detail = body.score_detail?.[0];
        if (detail) {
          detail.rerank_score = 0.987;
          detail.rerank_applied = true;
          body.rerank_applied = true;
        }
        await route.fulfill({ response, json: body });
      },
      { times: 1 },
    );
    const retrievalResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}/retrieve`),
    );
    await page.locator('button[title="检索"]').click();
    const firstRetrieval = await retrievalResponse;
    expect(firstRetrieval.status()).toBe(200);
    const firstRetrievalBody = (await firstRetrieval.json()) as {
      retrieval_mode: string;
      rerank_applied: boolean;
      score_detail?: Array<{
        vector_score?: number | null;
        keyword_score?: number | null;
        fused_score?: number | null;
        rerank_score?: number | null;
      }> | null;
    };
    expect(firstRetrievalBody.retrieval_mode).toBe("hybrid");
    expect(firstRetrievalBody.rerank_applied).toBe(true);
    expect(firstRetrievalBody.score_detail?.length).toBeGreaterThan(0);
    expect(firstRetrievalBody.score_detail?.[0]?.vector_score).toEqual(expect.any(Number));
    expect(firstRetrievalBody.score_detail?.[0]?.keyword_score).toEqual(expect.any(Number));
    expect(firstRetrievalBody.score_detail?.[0]?.fused_score).toEqual(expect.any(Number));
    expect(firstRetrievalBody.score_detail?.[0]?.rerank_score).toBe(0.987);
    await expect(page.getByText(/AgentCanvas deployment uses a canary rollout/)).toBeVisible();
    await expect(page.getByText(/^vector score/).first()).toBeVisible();
    await expect(page.getByText(/^keyword score/).first()).toBeVisible();
    await expect(page.getByText(/^fused score/).first()).toBeVisible();
    await expect(page.getByText(/^rerank score/).first()).toBeVisible();
    await expect(page.locator("mark").filter({ hasText: "canary" }).first()).toBeVisible();

    await page.getByLabel("Top K", { exact: true }).fill("3");
    const comparisonResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}/retrieve`),
    );
    await page.getByLabel("最低分数", { exact: true }).fill("0");
    expect((await comparisonResponse).status()).toBe(200);
    await expect(page.getByText("A / 上一轮", { exact: true })).toBeVisible();
    await expect(page.getByText("B / 当前", { exact: true })).toBeVisible();

    const queryInput = page.getByPlaceholder("输入检索查询");
    await queryInput.fill("different deployment question");
    await expect(page.getByText("A / 上一轮", { exact: true })).toHaveCount(0);
    await expect(page.getByText("B / 当前", { exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "保存评测样本", exact: true }),
    ).toHaveCount(0);
    await queryInput.fill("canary rollout health checks");

    const refreshedResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}/retrieve`),
    );
    await page.locator('button[title="检索"]').click();
    expect((await refreshedResponse).status()).toBe(200);

    await page.getByRole("button", { name: "保存评测样本", exact: true }).click();
    const caseDialog = page.locator('[aria-labelledby="retrieval-case-dialog-title"]');
    await expect(caseDialog).toBeVisible();
    const axe = await new AxeBuilder({ page })
      .include('[aria-labelledby="retrieval-case-dialog-title"]')
      .analyze();
    const blocking = axe.violations.filter(
      (violation) => violation.impact === "critical" || violation.impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
    await caseDialog.getByLabel("数据集名称", { exact: true }).fill(datasetName);
    await caseDialog.getByLabel("输入变量", { exact: true }).fill("user_query");
    const datasetResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/evaluation-datasets"),
    );
    await caseDialog.getByRole("button", { name: "保存样本", exact: true }).click();
    const createdDatasetResponse = await datasetResponse;
    expect(createdDatasetResponse.status()).toBe(201);
    const createdDataset = (await createdDatasetResponse.json()) as {
      versions: Array<{
        cases: Array<{
          inputs: Record<string, unknown>;
          expected: { relevant_chunk_ids?: string[] };
        }>;
      }>;
    };
    expect(createdDataset.versions[0]?.cases[0]?.inputs).toEqual({
      user_query: "canary rollout health checks",
    });
    expect(createdDataset.versions[0]?.cases[0]?.expected.relevant_chunk_ids).toContain(
      `${documentId}:0`,
    );

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);

    const workflow = await createRagWorkflow(request, `RAG E2E ${suffix}`, knowledgeBaseId);
    workflowId = workflow.id;
    const workflowName = `RAG E2E ${suffix}`;
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const runDialog = page.locator('[aria-labelledby="run-dialog-title"]');
    await runDialog.locator("input").first().fill("How should deployment be promoted?");
    await runDialog.getByRole("button", { name: "开始运行" }).click();
    await expect(page.getByText("retrieval", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("workflow_finished", { exact: true })).toBeVisible();
    await expect(page.getByText("Sources / 1", { exact: true })).toBeVisible();
    await expect(page.getByText("e2e-handbook.md", { exact: true }).last()).toBeVisible();

    const publishedResponse = await request.post(
      `${API_URL}/api/workflows/${workflowId}/publish`,
      { headers: adminHeaders },
    );
    expect(publishedResponse.status()).toBe(200);
    const publishedVersion = (await publishedResponse.json()) as { id: string };
    await page.goto("/evaluations");
    await page
      .getByRole("complementary")
      .first()
      .getByRole("button", { name: new RegExp(`^${datasetName}`) })
      .click();
    await expect(page.getByRole("heading", { name: datasetName, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "运行评测", exact: true }).click();
    const evaluationDialog = page.locator('[aria-labelledby="evaluation-run-dialog-title"]');
    await evaluationDialog.getByLabel("工作流").selectOption({ label: workflowName });
    await expect(evaluationDialog.getByLabel("已发布版本").locator("option")).toHaveCount(2);
    await evaluationDialog.getByLabel("已发布版本").selectOption(publishedVersion.id);
    await evaluationDialog.getByRole("button", { name: "RAG", exact: true }).click();
    await expect(evaluationDialog.getByLabel("RAG 节点")).toHaveValue("retrieve");
    await expect(evaluationDialog.getByLabel("引用 Agent")).toHaveValue("agent");
    await evaluationDialog.getByLabel("Recall@k", { exact: true }).fill("3");
    const evaluationResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/evaluation-runs"),
    );
    await evaluationDialog.getByRole("button", { name: "开始评测", exact: true }).click();
    expect((await evaluationResponse).status()).toBe(201);
    await expect(page.getByText(/Recall@3=1\.000, MRR=1\.000/)).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("retrieval evidence", { exact: false })).toBeVisible();
    await expect(page.getByText("Recall@3", { exact: true })).toBeVisible();
    await expect(page.getByText("MRR", { exact: true })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    const reportOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(reportOverflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath("rag-evaluation-mobile.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: testInfo.outputPath("rag-evaluation-desktop.png"), fullPage: true });

    await page.goto("/knowledge");
    page.once("dialog", (confirmation) => void confirmation.accept());
    const deleteResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().endsWith(`/api/knowledge-bases/${knowledgeBaseId}`),
    );
    await page.locator('button[title="删除知识库"]').click();
    expect((await deleteResponse).status()).toBe(204);
    knowledgeDeleted = true;
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (workflowId) await cleanupWorkflow(request, workflowId, bodyCompleted);
    if (knowledgeBaseId && !knowledgeDeleted) {
      const response = await request.delete(`${API_URL}/api/knowledge-bases/${knowledgeBaseId}`, {
        headers: adminHeaders,
      });
      if (bodyCompleted) expect(response.status()).toBe(204);
    }
  }
});
