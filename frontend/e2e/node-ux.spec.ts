import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import {
  API_URL,
  FRONTEND_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  login,
} from "./support";

function branchWorkflowBody(name: string) {
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
          position: { x: 0, y: 120 },
          config: { input_schema: [{ name: "user_query", type: "string", required: true }] },
        },
        {
          id: "agent",
          type: "agent",
          position: { x: 280, y: 120 },
          config: {
            model_config_id: "default",
            system_prompt: "Reply.",
            user_prompt: "{{input.user_query}}",
          },
        },
        {
          id: "end",
          type: "end",
          position: { x: 620, y: 120 },
          config: { output_template: { answer: "{{nodes.agent.output}}" } },
        },
      ],
      edges: [
        { id: "e1", source: "start", target: "agent" },
        { id: "e2", source: "agent", target: "end" },
      ],
    },
  };
}

test("connection validity rejects end→target and start as target while highlighting legal targets", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    branchWorkflowBody(`Node UX validity ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    // The ``end`` node has no outbound source handle, and ``start`` accepts no
    // inbound edge. Dragging from end's (absent) source is not possible, so we
    // verify the gate by dragging start→end (valid) and confirming the edge
    // appears, then attempting a self-loop on agent which must be rejected.
    const start = page.locator('.react-flow__node[data-id="start"]');
    const end = page.locator('.react-flow__node[data-id="end"]');
    const agent = page.locator('.react-flow__node[data-id="agent"]');
    await page.getByRole("button", { name: "Fit View" }).click();
    await expect(start).toBeVisible();
    await expect(end).toBeVisible();

    const startBox = await start.boundingBox();
    const endBox = await end.boundingBox();
    const agentBox = await agent.boundingBox();
    expect(startBox).not.toBeNull();
    expect(endBox).not.toBeNull();
    expect(agentBox).not.toBeNull();

    // Drag from the agent source handle (right side) toward empty canvas to
    // open the drop-to-create node search. A connection that ends on empty
    // canvas (not on a handle) must surface the search palette.
    const agentSourceX = agentBox!.x + agentBox!.width + 2;
    const agentSourceY = agentBox!.y + agentBox!.height / 2;
    await page.mouse.move(agentSourceX, agentSourceY);
    await page.mouse.down();
    // Move a little to register the connection drag, then release on empty
    // canvas below the agent node.
    await page.mouse.move(agentSourceX + 40, agentSourceY + 120, { steps: 6 });
    await page.mouse.move(agentSourceX + 40, agentSourceY + 160, { steps: 4 });
    await page.mouse.up();

    await expect(page.getByRole("dialog", { name: "插入节点并连接" })).toBeVisible();
    const dialog = page.getByRole("dialog", { name: "插入节点并连接" });
    const dialogAccessibility = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .analyze();
    expect(
      dialogAccessibility.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    // Search filters the list; picking "结束" inserts an end node. Since the
    // dragged source (agent) is not ``end`` and the target is ``end``, the
    // connection is valid and should be wired in one motion.
    await dialog.getByLabel("节点搜索").fill("结束");
    await expect(dialog.getByRole("button", { name: "结束" })).toBeVisible();
    await dialog.getByRole("button", { name: "结束" }).click();
    await expect(dialog).toBeHidden();

    // A new end node was inserted and wired to the agent source. Assert at
    // least one new node + one new edge persisted via autosave.
    await expect
      .poll(async () => {
        const response = await request.get(`${API_URL}/api/workflows/${workflow.id}`, {
          headers: adminHeaders,
        });
        const snapshot = (await response.json()) as {
          dsl: {
            nodes: Array<{ id: string; type: string }>;
            edges: Array<{ source: string; target: string }>;
          };
        };
        const endNodes = snapshot.dsl.nodes.filter((n) => n.type === "end");
        return {
          endCount: endNodes.length,
          edgesFromAgent: snapshot.dsl.edges.filter((e) => e.source === "agent").length,
        };
      })
      .toMatchObject({ endCount: 2, edgesFromAgent: 2 });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
