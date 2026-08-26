import { expect, test } from "@playwright/test";

import {
  API_URL,
  FRONTEND_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
} from "./support";

function canvasObjectsWorkflow(name: string) {
  return {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        { id: "start", type: "start", position: { x: 80, y: 160 }, config: {} },
        { id: "agent", type: "agent", position: { x: 360, y: 160 }, config: {} },
        { id: "end", type: "end", position: { x: 640, y: 160 }, config: {} },
      ],
      edges: [
        { id: "start-agent", source: "start", target: "agent" },
        { id: "agent-end", source: "agent", target: "end" },
      ],
    },
  };
}

type Snapshot = {
  dsl: {
    nodes: Array<{ id: string; position: { x: number; y: number } }>;
    edges: Array<{ id: string; label?: string | null }>;
    canvas: {
      groups: Array<{ id: string; name: string; node_ids: string[]; collapsed: boolean }>;
      notes: Array<{ id: string; text: string }>;
    };
  };
};

async function readSnapshot(request: Parameters<typeof createWorkflowFromBody>[0], id: string) {
  const response = await request.get(`${API_URL}/api/workflows/${id}`, {
    headers: adminHeaders,
  });
  expect(response.ok()).toBe(true);
  return (await response.json()) as Snapshot;
}

test("canvas groups, notes, edge labels, and version diff persist", async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    canvasObjectsWorkflow(`Canvas objects ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    const start = page.locator('.react-flow__node[data-id="start"]');
    const end = page.locator('.react-flow__node[data-id="end"]');
    await expect(start).toBeVisible();
    await expect(end).toBeVisible();
    await page.getByRole("button", { name: "Fit View" }).click();
    await start.click();
    await end.click({ modifiers: ["Control"] });
    await expect(start).toHaveClass(/selected/);
    await expect(end).toHaveClass(/selected/);
    await page.getByRole("button", { name: "添加分组" }).click();

    const group = page.locator('[data-testid^="canvas-group-"]').first();
    await expect(group).toBeVisible();
    const groupName = group.locator('input[aria-label^="分组名称"]');
    await groupName.fill("Release path");
    await expect(group.getByRole("button", { name: "折叠分组 Release path" })).toBeVisible();

    await group.getByRole("button", { name: "折叠分组 Release path" }).click();
    await expect(start).toBeHidden();
    await expect(end).toBeHidden();
    await group.getByRole("button", { name: "展开分组 Release path" }).click();
    await expect(start).toBeVisible();
    await expect(end).toBeVisible();

    const header = group.locator("div").first();
    const headerBox = await header.boundingBox();
    expect(headerBox).not.toBeNull();
    await page.mouse.move(headerBox!.x + 120, headerBox!.y + headerBox!.height / 2);
    await page.mouse.down();
    await page.mouse.move(headerBox!.x + 160, headerBox!.y + headerBox!.height / 2, { steps: 6 });
    await page.mouse.up();

    await page.getByRole("button", { name: "添加便签" }).click();
    const note = page.locator('[data-testid^="canvas-note-"]').first();
    await expect(note).toBeVisible();
    await note.getByLabel("便签内容").fill("Release intent");
    await note.getByLabel("便签内容").blur();

    await page.locator('.react-flow__edge[data-id="agent-end"]').click();
    const edgeLabel = page.getByLabel("边标签");
    await expect(edgeLabel).toBeVisible();
    await edgeLabel.fill("success");
    await edgeLabel.blur();

    await expect(page.getByText("synced", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, workflow.id);
        return {
          group: snapshot.dsl.canvas.groups[0],
          note: snapshot.dsl.canvas.notes[0]?.text,
          edge: snapshot.dsl.edges.find((edge) => edge.id === "agent-end")?.label,
        };
      })
      .toMatchObject({
        group: { name: "Release path", node_ids: ["start", "end"], collapsed: false },
        note: "Release intent",
        edge: "success",
      });

    const versionsResponse = await request.get(`${API_URL}/api/workflows/${workflow.id}/versions`, {
      headers: adminHeaders,
    });
    expect(versionsResponse.ok()).toBe(true);
    const versions = (await versionsResponse.json()) as Array<{ id: string; number: number }>;
    expect(versions.length).toBeGreaterThan(1);
    await page.locator('button[title="版本历史"]').click();
    const dialog = page.getByRole("dialog", { name: "版本历史" });
    await dialog.getByLabel(`选择版本 ${versions.at(-1)!.number} 用于对比`).click();
    await dialog.getByLabel(`选择版本 ${versions[0]!.number} 用于对比`).click();
    await expect(dialog.getByText(/canvas objects changed/)).toBeVisible();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
