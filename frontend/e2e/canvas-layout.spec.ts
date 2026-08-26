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

function layoutWorkflowBody(name: string) {
  return {
    name,
    dsl: {
      version: "1.0",
      name,
      variables: [],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 50 },
      nodes: [
        { id: "start", type: "start", position: { x: 260, y: 220 } },
        { id: "left", type: "agent", position: { x: 40, y: 60 }, config: {} },
        { id: "right", type: "agent", position: { x: 480, y: 260 }, config: {} },
        { id: "end", type: "end", position: { x: 180, y: 360 }, config: {} },
      ],
      edges: [
        { id: "start-left", source: "start", target: "left" },
        { id: "start-right", source: "start", target: "right" },
        { id: "left-end", source: "left", target: "end" },
        { id: "right-end", source: "right", target: "end" },
      ],
    },
  };
}

type WorkflowSnapshot = {
  dsl: {
    nodes: Array<{ id: string; position: { x: number; y: number } }>;
  };
};

async function readSnapshot(request: Parameters<typeof createWorkflowFromBody>[0], id: string) {
  const response = await request.get(`${API_URL}/api/workflows/${id}`, {
    headers: adminHeaders,
  });
  expect(response.ok()).toBe(true);
  return (await response.json()) as WorkflowSnapshot;
}

test("canvas layout supports auto layout, multi-select commands, and snap guides", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const workflow = await createWorkflowFromBody(
    request,
    layoutWorkflowBody(`Canvas layout ${Date.now()}-${test.info().workerIndex}`),
  );
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    const layoutButton = page.getByRole("button", { name: "布局与对齐" });
    const menu = page.getByRole("menu", { name: "布局与对齐操作" });
    await layoutButton.click();
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "自动布局" })).toBeFocused();
    const menuAccessibility = await new AxeBuilder({ page })
      .include('[role="menu"]')
      .analyze();
    expect(
      menuAccessibility.violations.filter((violation) =>
        ["critical", "serious"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(layoutButton).toBeFocused();

    await layoutButton.click();
    await menu.getByRole("menuitem", { name: "自动布局" }).click();
    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, workflow.id);
        const positions = new Map(snapshot.dsl.nodes.map((node) => [node.id, node.position]));
        const start = positions.get("start")!;
        const branchLeft = positions.get("left")!;
        const branchRight = positions.get("right")!;
        const finish = positions.get("end")!;
        return start.x < branchLeft.x && start.x < branchRight.x && finish.x > branchLeft.x && finish.x > branchRight.x;
      })
      .toBe(true);
    await expect(page.getByRole("button", { name: "撤销", exact: true })).toBeEnabled();

    const left = page.locator('.react-flow__node[data-id="left"]');
    const right = page.locator('.react-flow__node[data-id="right"]');
    const end = page.locator('.react-flow__node[data-id="end"]');
    await expect(left).toBeVisible();
    await expect(right).toBeVisible();
    await expect(end).toBeVisible();
    await left.click();
    await right.click({ modifiers: ["Control"] });
    await end.click({ modifiers: ["Control"] });

    await layoutButton.click();
    await expect(menu.getByText("对齐 · 已选 3")).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "自动布局" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "左对齐" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "水平居中" })).toBeFocused();
    await page.keyboard.press("Escape");

    await layoutButton.click();
    await menu.getByRole("menuitem", { name: "顶端对齐" }).click();
    await expect
      .poll(async () => {
        const snapshot = await readSnapshot(request, workflow.id);
        const positions = new Map(snapshot.dsl.nodes.map((node) => [node.id, node.position]));
        return new Set(["left", "right", "end"].map((id) => positions.get(id)?.y)).size;
      })
      .toBe(1);

    await layoutButton.click();
    await menu.getByRole("menuitem", { name: "水平等距" }).click();
    await expect
      .poll(async () => {
        const boxes = await Promise.all(
          [left, right, end].map(async (node) => ({
            box: await node.boundingBox(),
          })),
        );
        if (boxes.some((entry) => entry.box === null)) return null;
        const ordered = boxes
          .map((entry) => entry.box!)
          .sort((a, b) => a.x - b.x);
        const gaps = [
          ordered[1].x - (ordered[0].x + ordered[0].width),
          ordered[2].x - (ordered[1].x + ordered[1].width),
        ];
        return Math.abs(gaps[0] - gaps[1]);
      })
      .toBeLessThan(3);
    const distributedBoxes = await Promise.all(
      [left, right, end].map(async (node) => ({ box: await node.boundingBox() })),
    );
    const distributed = distributedBoxes
      .map((entry) => entry.box)
      .filter((box): box is NonNullable<typeof box> => box !== null)
      .sort((a, b) => a.x - b.x);
    expect(distributed.length).toBe(3);
    const distributedGaps = [
      distributed[1].x - (distributed[0].x + distributed[0].width),
      distributed[2].x - (distributed[1].x + distributed[1].width),
    ];
    expect(Math.min(...distributedGaps)).toBeGreaterThanOrEqual(-1);
    expect(Math.abs(distributedGaps[0] - distributedGaps[1])).toBeLessThan(3);

    await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
    await left.click();
    const leftBox = await left.boundingBox();
    const rightBox = await right.boundingBox();
    expect(leftBox).not.toBeNull();
    expect(rightBox).not.toBeNull();
    await page.mouse.move(leftBox!.x + 20, leftBox!.y + leftBox!.height / 2);
    await page.mouse.down();
    await page.mouse.move(rightBox!.x + 20, leftBox!.y + leftBox!.height / 2, {
      steps: 20,
    });
    await page.mouse.move(rightBox!.x - leftBox!.width + 20, leftBox!.y + leftBox!.height / 2, {
      steps: 20,
    });
    await expect
      .poll(() => page.getByTestId("alignment-guide").count())
      .toBeGreaterThan(0);
    await page.mouse.up();

    await expect(page.getByText("已完成自动布局", { exact: true })).toHaveCount(0);
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});

test("snap guide remains visible for an exact zero-offset alignment", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const body = layoutWorkflowBody(
    `Canvas exact snap ${Date.now()}-${test.info().workerIndex}`,
  );
  body.dsl.nodes = [
    { id: "start", type: "start", position: { x: 120, y: 80 } },
    { id: "left", type: "agent", position: { x: 120, y: 220 }, config: {} },
    { id: "end", type: "end", position: { x: 420, y: 360 }, config: {} },
  ];
  body.dsl.edges = [
    { id: "start-left", source: "start", target: "left" },
    { id: "left-end", source: "left", target: "end" },
  ];
  const workflow = await createWorkflowFromBody(request, body);
  let bodyCompleted = false;

  try {
    await page.goto(`${FRONTEND_URL}/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.getByTestId("collaboration-status")).toContainText("我在编辑");

    const moving = page.locator('.react-flow__node[data-id="left"]');
    const box = await moving.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box!.x + box!.width / 2,
      box!.y + box!.height / 2 + 24,
      { steps: 8 },
    );
    await expect
      .poll(() => page.getByTestId("alignment-guide").count())
      .toBeGreaterThan(0);
    await page.mouse.up();

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, bodyCompleted);
  }
});
