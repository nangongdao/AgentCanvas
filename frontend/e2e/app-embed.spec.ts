import { createServer } from "node:http";

import { expect, test } from "@playwright/test";

import {
  API_URL,
  FRONTEND_URL,
  TOKENS,
  captureUnexpectedErrors,
  workflowBody,
} from "./support";

const PASSWORD = "BrowserPass!2026";

// The embedder host page runs on a second loopback origin: a different port
// makes it a genuinely different origin from the platform (proving the script
// works cross-origin) while staying inside the loopback address space so
// Chromium's private-network-access rules do not block the script request the
// way they would for a synthetic public host reaching into 127.0.0.1.
const HOST_PORT = 5174;

test("floating bubble script embeds a public app into an external page", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const suffix = `${Date.now()}-${test.info().workerIndex}`;
  const ownerEmail = `embed-owner-${suffix}@example.test`;

  const registered = await request.post(`${API_URL}/api/auth/register`, {
    headers: { Authorization: `Bearer ${TOKENS.admin}` },
    data: {
      email: ownerEmail,
      password: PASSWORD,
      display_name: "Embed Owner",
      role: "admin",
    },
  });
  expect(registered.status()).toBe(201);
  const login = await request.post(`${API_URL}/api/auth/login`, {
    data: { email: ownerEmail, password: PASSWORD },
  });
  expect(login.status()).toBe(200);

  const organization = await request.post(`${API_URL}/api/organizations`, {
    data: { name: `Embed Org ${suffix}` },
  });
  expect(organization.status()).toBe(201);
  const organizationId = ((await organization.json()) as { id: string }).id;
  const project = await request.post(
    `${API_URL}/api/organizations/${organizationId}/projects`,
    { data: { name: `Embed Project ${suffix}` } },
  );
  expect(project.status()).toBe(201);
  const projectId = ((await project.json()) as { id: string }).id;

  const workflow = await request.post(`${API_URL}/api/workflows`, {
    data: {
      ...workflowBody(`Embed Workflow ${suffix}`),
      project_id: projectId,
    },
  });
  expect(workflow.status()).toBe(201);
  const workflowId = ((await workflow.json()) as { id: string }).id;
  const published = await request.post(
    `${API_URL}/api/workflows/${workflowId}/publish`,
  );
  expect(published.status()).toBe(200);

  const versions = await request.get(
    `${API_URL}/api/workflows/${workflowId}/versions`,
  );
  expect(versions.status()).toBe(200);
  const versionRows = (await versions.json()) as Array<{ id: string; status: string }>;
  const versionId = versionRows.find((row) => row.status === "published")?.id;
  expect(versionId).toBeTruthy();

  const created = await request.post(`${API_URL}/api/apps`, {
    data: {
      project_id: projectId,
      workflow_id: workflowId,
      name: "Embed Bubble App",
      visibility: "public",
      welcome_message: "Hello from embed",
      embed_allowed_origins: [`http://127.0.0.1:${HOST_PORT}`],
    },
  });
  expect(created.status()).toBe(201);
  const createdBody = (await created.json()) as {
    app: { id: string; slug: string };
  };
  const bound = await request.post(
    `${API_URL}/api/apps/${createdBody.app.id}/version`,
    { data: { version_id: versionId } },
  );
  expect(bound.status()).toBe(200);

  const hostServer = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><html><body><h1>External site</h1>
<script src="${FRONTEND_URL}/api/apps/p/${createdBody.app.slug}/embed.js" data-title="Support chat" async></script>
</body></html>`);
  });
  await new Promise<void>((resolve) =>
    hostServer.listen(HOST_PORT, "127.0.0.1", resolve),
  );

  try {
    await page.goto(`http://127.0.0.1:${HOST_PORT}/`);
    await expect(
      page.getByRole("heading", { name: "External site" }),
    ).toBeVisible();

    // The bootstrap mounts a floating bubble driven by data-title.
    const bubble = page.locator('button[aria-label="Support chat"]');
    await expect(bubble).toBeVisible();

    // Opening lazily creates the iframe pointing at the standalone runtime.
    await bubble.click();
    const panel = page.locator('div[role="dialog"][aria-label="Support chat"]');
    await expect(panel).toBeVisible();
    const frame = panel.locator("iframe");
    await expect(frame).toHaveAttribute(
      "src",
      new RegExp(`/apps/p/${createdBody.app.slug}\\?embed=1$`),
    );

    // The runtime page renders inside the iframe without a platform session.
    const runtime = page.frameLocator('div[role="dialog"] iframe');
    await expect(runtime.getByText("Hello from embed")).toBeVisible({
      timeout: 20_000,
    });
    await expect(runtime.getByPlaceholder("输入消息，Enter 发送")).toBeVisible();

    // Escape collapses the panel while the bubble stays available. Focus the
    // host page first: after the runtime page auto-focuses its chat input the
    // iframe owns the keyboard, and host-level Escape handling applies to the
    // embedder's document.
    await bubble.focus();
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(bubble).toBeVisible();

    // Clicking the bubble again also toggles the panel closed.
    await bubble.click();
    await expect(panel).toBeVisible();
    await bubble.click();
    await expect(panel).toBeHidden();
  } finally {
    await new Promise<void>((resolve) => hostServer.close(() => resolve()));
  }

  expect(errors.pageErrors).toEqual([]);
  expect(errors.consoleErrors).toEqual([]);
  expect(errors.httpErrors).toEqual([]);
});
