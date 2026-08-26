import { expect, type APIRequestContext, type Page } from "@playwright/test";

const backendPort = process.env.AGENTCANVAS_E2E_BACKEND_PORT ?? "8000";
const frontendPort = process.env.AGENTCANVAS_E2E_FRONTEND_PORT ?? "5173";

export const API_URL = `http://127.0.0.1:${backendPort}`;
export const FRONTEND_URL = `http://127.0.0.1:${frontendPort}`;
export const TOKENS = {
  viewer: "viewer-e2e-token-20260729",
  editor: "editor-e2e-token-20260729",
  admin: "admin-e2e-token-20260729",
} as const;

export type Role = keyof typeof TOKENS;

export const adminHeaders = {
  Authorization: `Bearer ${TOKENS.admin}`,
};
export const editorHeaders = {
  Authorization: `Bearer ${TOKENS.editor}`,
};

export function workflowBody(name: string) {
  return {
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
          position: { x: 0, y: 100 },
          config: { input_schema: [{ name: "user_query", type: "string", required: true }] },
        },
        {
          id: "end",
          type: "end",
          position: { x: 360, y: 100 },
          config: { output_template: { answer: "{{input.user_query}}" } },
        },
      ],
      edges: [{ id: "start-end", source: "start", target: "end" }],
    },
  };
}

export function ragWorkflowBody(name: string, kbId: string) {
  return {
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
          position: { x: 0, y: 100 },
          config: { input_schema: [{ name: "user_query", type: "string", required: true }] },
        },
        {
          id: "retrieve",
          type: "rag",
          position: { x: 260, y: 100 },
          config: { kb_id: kbId, query: "{{input.user_query}}", top_k: 3, score_threshold: 0 },
        },
        {
          id: "agent",
          type: "agent",
          position: { x: 520, y: 100 },
          config: {
            model_config_id: "default",
            system_prompt: "Answer using approved context.",
            user_prompt: "{{input.user_query}}",
            context_nodes: ["retrieve"],
          },
        },
        {
          id: "end",
          type: "end",
          position: { x: 780, y: 100 },
          config: { output_template: { answer: "{{nodes.agent.output}}" } },
        },
      ],
      edges: [
        { id: "e1", source: "start", target: "retrieve" },
        { id: "e2", source: "retrieve", target: "agent" },
        { id: "e3", source: "agent", target: "end" },
      ],
    },
  };
}

export function humanWorkflowBody(name: string) {
  return {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        { id: "start", type: "start", position: { x: 0, y: 100 } },
        {
          id: "approval",
          type: "human",
          position: { x: 300, y: 100 },
          config: {
            title: "Release approval",
            instruction: "Approve the release before it continues.",
          },
        },
        { id: "end", type: "end", position: { x: 600, y: 100 } },
      ],
      edges: [
        { id: "e1", source: "start", target: "approval" },
        { id: "e2", source: "approval", target: "end" },
      ],
    },
  };
}

export function chatWorkflowBody(name: string) {
  return {
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
          position: { x: 0, y: 100 },
          config: { input_schema: [{ name: "user_query", type: "string", required: true }] },
        },
        {
          id: "agent",
          type: "agent",
          position: { x: 300, y: 100 },
          config: {
            model_config_id: "default",
            system_prompt: "Reply to the user.",
            user_prompt: "{{input.user_query}}",
          },
        },
        { id: "end", type: "end", position: { x: 600, y: 100 } },
      ],
      edges: [
        { id: "e1", source: "start", target: "agent" },
        { id: "e2", source: "agent", target: "end" },
      ],
    },
  };
}

export async function createWorkflow(request: APIRequestContext, name: string) {
  const response = await request.post(`${API_URL}/api/workflows`, {
    headers: adminHeaders,
    data: workflowBody(name),
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { id: string };
}

export async function createWorkflowFromBody(
  request: APIRequestContext,
  body: Record<string, unknown>,
) {
  const response = await request.post(`${API_URL}/api/workflows`, {
    headers: adminHeaders,
    data: body,
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { id: string };
}

export async function createRagWorkflow(
  request: APIRequestContext,
  name: string,
  kbId: string,
) {
  return createWorkflowFromBody(request, ragWorkflowBody(name, kbId));
}

export async function archiveWorkflow(request: APIRequestContext, id: string) {
  const response = await request.delete(`${API_URL}/api/workflows/${id}`, {
    headers: adminHeaders,
  });
  expect(response.status()).toBe(204);
}

export async function cleanupWorkflow(
  request: APIRequestContext,
  id: string,
  bodyCompleted: boolean,
) {
  try {
    await archiveWorkflow(request, id);
  } catch (error) {
    if (bodyCompleted) throw error;
  }
}

export async function login(page: Page, role: Role) {
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  // Seed the canvas-tour dismissal for every post-login navigation: each
  // test runs in a fresh browser, and the first-visit tour modal would
  // otherwise block canvas interactions across the suite. The onboarding
  // spec removes the marker to exercise the real tour lifecycle.
  await page.addInitScript(() =>
    localStorage.setItem("agentcanvas:canvas-tour", "done"),
  );
  await page.getByRole("button", { name: "API Token", exact: true }).click();
  await page.getByLabel("API Token").fill(TOKENS[role]);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await expect(page.locator('button[title="当前访问角色与偏好"]')).toContainText(role);
}

export async function logout(page: Page) {
  await page.locator('button[title="当前访问角色与偏好"]').click();
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "退出登录", exact: true }).click(),
  ]);
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
}

export function captureUnexpectedErrors(page: Page) {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const httpErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().includes("401") &&
      !message.text().startsWith("Failed to load resource")
    ) {
      consoleErrors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 400 && response.status() !== 401) {
      // External font CDNs are outside the application's contract; a transient
      // woff2 404 must not fail platform error accounting.
      const host = new URL(response.url()).host;
      if (host === "fonts.gstatic.com" || host === "fonts.googleapis.com") {
        return;
      }
      httpErrors.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "unknown";
    if (request.url().endsWith("/api/auth/logout") && failure === "net::ERR_ABORTED") return;
    if (
      request.url().includes("/api/chat/sessions/") &&
      request.url().endsWith("/send") &&
      failure === "net::ERR_ABORTED"
    ) {
      return;
    }
    if (request.url().startsWith(API_URL)) {
      httpErrors.push(`FAILED ${request.url()}: ${failure}`);
    }
  });
  return { pageErrors, consoleErrors, httpErrors };
}
