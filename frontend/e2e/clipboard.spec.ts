import { expect, test } from "@playwright/test";

import {
  API_URL,
  FRONTEND_URL,
  adminHeaders,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  humanWorkflowBody,
  login,
} from "./support";

type Snapshot = {
  dsl: {
    nodes: Array<{ id: string; type: string; position: { x: number; y: number } }>;
    edges: Array<{ id: string; source: string; target: string }>;
  };
};

async function readSnapshot(
  request: Parameters<typeof createWorkflowFromBody>[0],
  id: string,
) {
  const response = await request.get(`${API_URL}/api/workflows/${id}`, {
    headers: adminHeaders,
  });
  expect(response.ok()).toBe(true);
  return (await response.json()) as Snapshot;
}

async function selectNodes(
  page: import("@playwright/test").Page,
  ids: string[],
) {
  await page.getByRole("button", { name: "Fit View" }).click();
  for (const id of ids) {
    const node = page.locator(`.react-flow__node[data-id="${id}"]`);
    await expect(node).toBeVisible();
    if (ids.indexOf(id) === 0) {
      await node.click();
    } else {
      await node.click({ modifiers: ["Control"] });
    }
    await expect(node).toHaveClass(/selected/);
  }
}

test("subgraph copy and paste rebuilds ids and preserves internal edges", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`Clipboard paste ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    // Select approval + end (the subgraph with the internal e2 edge).
    await selectNodes(page, ["approval", "end"]);
    await page.getByRole("button", { name: "复制" }).click();
    await expect(page.getByRole("button", { name: "粘贴" })).toBeEnabled();
    await page.getByRole("button", { name: "粘贴" }).click();

    // The original approval/end remain; two new nodes are added and selected.
    const nodeCount = await page.locator(".react-flow__node").count();
    expect(nodeCount).toBe(5);
    const selected = page.locator(".react-flow__node.selected");
    await expect.poll(async () => await selected.count()).toBe(2);

    // The pasted subgraph keeps its internal wiring as a single new edge.
    const edgeCount = await page.locator(".react-flow__edge").count();
    expect(edgeCount).toBe(3);

    // Autosave (800ms debounce + network) persists the rebuilt subgraph; the
    // pasted nodes get fresh ids that never collide with the originals, and
    // the internal approval->end wiring is rebuilt onto those new ids. The
    // draft is allowed to be unreachable from start (strict=False on save)
    // until the editor wires it back in.
    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, workflow.id);
        const originals = new Set(["start", "approval", "end"]);
        const pasted = snapshot.dsl.nodes.filter((node) => !originals.has(node.id));
        const pastedIds = pasted.map((node) => node.id);
        return {
          pastedCount: pasted.length,
          pastedTypes: pasted.map((node) => node.type).sort(),
          pastedIdsUnique: new Set(pastedIds).size === pastedIds.length,
          noIdCollision: pastedIds.every((id) => !originals.has(id)),
          internalEdge: snapshot.dsl.edges.find(
            (edge) =>
              pastedIds.includes(edge.source) && pastedIds.includes(edge.target),
          ),
          totalNodes: snapshot.dsl.nodes.length,
          totalEdges: snapshot.dsl.edges.length,
        };
      })
      .toMatchObject({
        pastedCount: 2,
        pastedTypes: ["end", "human"],
        pastedIdsUnique: true,
        noIdCollision: true,
        totalNodes: 5,
        totalEdges: 3,
      });
    // The rebuilt internal edge must actually exist, not just be undefined.
    const finalSnapshot = await readSnapshot(request, workflow.id);
    const originals = new Set(["start", "approval", "end"]);
    const pastedIds = finalSnapshot.dsl.nodes
      .filter((node) => !originals.has(node.id))
      .map((node) => node.id);
    expect(
      finalSnapshot.dsl.edges.find(
        (edge) => pastedIds.includes(edge.source) && pastedIds.includes(edge.target),
      ),
    ).toBeDefined();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("subgraph pastes across workflows via the shared clipboard", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const source = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`Clipboard source ${Date.now()}-${test.info().workerIndex}`),
  );
  const target = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`Clipboard target ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    // Copy a subgraph from the source workflow.
    await page.goto(`${FRONTEND_URL}/workflows/${source.id}`);
    await login(page, "admin");
    await selectNodes(page, ["approval", "end"]);
    await page.getByRole("button", { name: "复制" }).click();

    // Navigate to a different workflow and paste; the clipboard survives.
    await page.goto(`${FRONTEND_URL}/workflows/${target.id}`);
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");
    await expect(page.getByRole("button", { name: "粘贴" })).toBeEnabled();
    await page.getByRole("button", { name: "粘贴" }).click();

    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, target.id);
        const ids = snapshot.dsl.nodes.map((node) => node.id);
        // The target workflow already ships start/approval/end nodes (from
        // humanWorkflowBody), so a buggy paste that reuses the source ids would
        // collide with them. Assert the pasted nodes received fresh ids and the
        // internal approval->end wiring was rebuilt onto those new ids.
        const originals = new Set(["start", "approval", "end"]);
        const pasted = snapshot.dsl.nodes.filter((node) => !originals.has(node.id));
        const pastedIds = pasted.map((node) => node.id);
        const internalEdge = snapshot.dsl.edges.find(
          (edge) => pastedIds.includes(edge.source) && pastedIds.includes(edge.target),
        );
        return {
          totalNodes: snapshot.dsl.nodes.length,
          totalEdges: snapshot.dsl.edges.length,
          pastedCount: pasted.length,
          idsUnique: new Set(ids).size === ids.length,
          noCollisionWithOriginals: pastedIds.every((id) => !originals.has(id)),
          internalEdgeRebuilt: internalEdge !== undefined,
        };
      })
      .toMatchObject({
        totalNodes: 5,
        totalEdges: 3,
        pastedCount: 2,
        idsUnique: true,
        noCollisionWithOriginals: true,
        internalEdgeRebuilt: true,
      });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, source.id, bodyCompleted);
    await cleanupWorkflow(request, target.id, bodyCompleted);
  }
});

test("paste merges into one undo step and keyboard shortcuts mirror the buttons", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    humanWorkflowBody(`Clipboard undo ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    // Copy a subgraph WITH an internal edge (approval->end) so a single undo
    // must revert both the pasted nodes and the rebuilt internal edge. A
    // single-node copy could not exercise the edge half of that contract.
    await selectNodes(page, ["approval", "end"]);
    // Ctrl+C / Ctrl+V keyboard shortcuts mirror the toolbar buttons.
    await page.keyboard.press("Control+c");
    await page.keyboard.press("Control+v");

    expect(await page.locator(".react-flow__node").count()).toBe(5);
    expect(await page.locator(".react-flow__edge").count()).toBe(3);

    // A single undo reverts the whole paste (nodes + the rebuilt internal edge).
    await page.keyboard.press("Control+z");
    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, workflow.id);
        const originals = new Set(["start", "approval", "end"]);
        const pasted = snapshot.dsl.nodes.filter((node) => !originals.has(node.id));
        // No pasted node should survive undo, and no edge should reference a
        // pasted id — catches a partial undo that removes nodes but leaves the
        // rebuilt internal edge dangling.
        const danglingEdge = snapshot.dsl.edges.find(
          (edge) => !originals.has(edge.source) || !originals.has(edge.target),
        );
        return {
          domNodes: await page.locator(".react-flow__node").count(),
          domEdges: await page.locator(".react-flow__edge").count(),
          persistedNodes: snapshot.dsl.nodes.length,
          persistedEdges: snapshot.dsl.edges.length,
          pastedSurvivors: pasted.length,
          danglingEdgeAfterUndo: danglingEdge !== undefined,
        };
      })
      .toMatchObject({
        domNodes: 3,
        domEdges: 2,
        persistedNodes: 3,
        persistedEdges: 2,
        pastedSurvivors: 0,
        danglingEdgeAfterUndo: false,
      });

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
