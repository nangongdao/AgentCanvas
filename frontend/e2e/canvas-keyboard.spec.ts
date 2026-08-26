import { expect, test } from "@playwright/test";

import {
  API_URL,
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflowFromBody,
  editorHeaders,
  login,
  workflowBody,
} from "./support";

test("canvas nodes are keyboard selectable, movable, and collapse nudges into one undo step", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    workflowBody(`Keyboard canvas ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "editor");
    await page.getByRole("button", { name: "Fit View" }).click();

    const startNode = page.locator('.react-flow__node[data-id="start"]');
    await expect(startNode).toBeVisible();

    // C5-11: nodes expose a screen-reader label with name and type.
    await expect(startNode).toHaveAttribute("aria-label", "开始");

    // Keyboard selection without a mouse: focus the node wrapper directly,
    // then Enter selects it (React Flow's built-in selection key).
    await startNode.focus();
    await expect(startNode).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(startNode).toHaveClass(/selected/);

    // Arrow keys nudge the node; read the rendered translate position.
    const positionOf = async () => {
      const transform = await startNode.evaluate((el) =>
        el.style.getPropertyValue("transform"),
      );
      const match = /translate\(([-0-9.]+)px,\s*([-0-9.]+)px\)/.exec(transform);
      return match ? { x: Number(match[1]), y: Number(match[2]) } : null;
    };
    const before = await positionOf();
    expect(before).not.toBeNull();

    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowDown");
    await page.waitForTimeout(120);
    const after = await positionOf();
    expect(after).not.toBeNull();
    expect(after!.x).toBeGreaterThan(before!.x);
    expect(after!.y).toBeGreaterThan(before!.y);

    // Movement reached the persisted DSL via autosave (dirty → save).
    // The start node begins at x=0, so any positive x proves persistence.
    await expect
      .poll(async () => {
        const snapshot = await request.get(`${API_URL}/api/workflows/${workflow.id}`, {
          headers: editorHeaders,
        });
        const body = (await snapshot.json()) as {
          dsl: { nodes: Array<{ id: string; position: { x: number; y: number } }> };
        };
        const start = body.dsl.nodes.find((node) => node.id === "start");
        return start ? start.position.x : -1;
      }, { timeout: 15_000 })
      .toBeGreaterThan(0);

    // The whole nudge run collapses into a single undo step: one Ctrl+Z
    // returns the node to its pre-keyboard position.
    await page.keyboard.press("Control+z");
    await page.waitForTimeout(120);
    const undone = await positionOf();
    expect(undone).toEqual(before);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
