import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { API_URL } from "./support";
const ADMIN_TOKEN = "admin-e2e-token-20260729";
const adminHeaders = { Authorization: `Bearer ${ADMIN_TOKEN}` };

declare global {
  interface Window {
    __AGENTCANVAS_DRAG_PAINTS__?: number[];
    __AGENTCANVAS_PERF__?: { commits: number[] };
    __AGENTCANVAS_LONG_TASKS__?: number[];
  }
}

function percentile(samples: number[], fraction: number): number {
  const sorted = [...samples].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)] ?? 0;
}

// Benchmark scale is configurable so the same spec serves the default-suite
// 100x500 gate and the C6-2 500x2000 large-canvas baseline
// (playwright.perf-scale.config.ts sets the env vars). Defaults keep the
// existing 100-node behavior byte-for-byte.
const NODE_COUNT = Number(process.env.AGENTCANVAS_PERF_NODES ?? 100);
const EDGE_COUNT = Number(process.env.AGENTCANVAS_PERF_EDGES ?? 500);
const GRID_COLUMNS = 10;

function largeWorkflowBody() {
  const nodes = Array.from({ length: NODE_COUNT }, (_, index) => ({
    id: `node-${index}`,
    type: index === 0 ? "start" : index === NODE_COUNT - 1 ? "end" : "agent",
    position: {
      x: (index % GRID_COLUMNS) * 220,
      y: Math.floor(index / GRID_COLUMNS) * 130,
    },
    config:
      index === 0
        ? { input_schema: [] }
        : index === NODE_COUNT - 1
          ? { output_template: {} }
          : { system_prompt: "Benchmark node", user_prompt: "Benchmark input" },
  }));
  const edges = Array.from({ length: NODE_COUNT - 1 }, (_, index) => ({
    id: `edge-chain-${index}`,
    source: `node-${index}`,
    target: `node-${index + 1}`,
  }));
  const existing = new Set(edges.map((edge) => `${edge.source}->${edge.target}`));
  // Spread additional DAG edges across the graph instead of creating a few
  // near-complete hubs whose visibility dominates the canvas benchmark.
  for (
    let offset = 2;
    offset < NODE_COUNT && edges.length < EDGE_COUNT;
    offset += 1
  ) {
    for (
      let source = 0;
      source + offset < NODE_COUNT && edges.length < EDGE_COUNT;
      source += 1
    ) {
      const target = source + offset;
      const key = `node-${source}->node-${target}`;
      if (existing.has(key)) continue;
      existing.add(key);
      edges.push({
        id: `edge-extra-${edges.length}`,
        source: `node-${source}`,
        target: `node-${target}`,
      });
    }
  }
  return {
    name: `Performance ${NODE_COUNT}x${EDGE_COUNT} ${Date.now()}`,
    dsl: {
      version: "1.0",
      name: `Performance ${NODE_COUNT}x${EDGE_COUNT}`,
      variables: [],
      settings: { max_loop_iterations: 20, timeout_seconds: 30, recursion_limit: 200 },
      nodes,
      edges,
    },
  };
}

async function createWorkflow(
  request: APIRequestContext,
): Promise<{ id: string; nodeCount: number; edgeCount: number }> {
  const response = await request.post(`${API_URL}/api/workflows`, {
    headers: adminHeaders,
    data: largeWorkflowBody(),
  });
  expect(response.status(), await response.text()).toBe(201);
  const created = (await response.json()) as {
    id: string;
    dsl: { nodes: unknown[]; edges: unknown[] };
  };
  return {
    id: created.id,
    nodeCount: created.dsl.nodes.length,
    edgeCount: created.dsl.edges.length,
  };
}

async function login(page: Page) {
  // Dismiss the first-visit canvas tour suite-wide (see support.login).
  await page.addInitScript(() =>
    localStorage.setItem("agentcanvas:canvas-tour", "done"),
  );
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await page.getByRole("button", { name: "API Token", exact: true }).click();
  await page.getByLabel("API Token").fill(ADMIN_TOKEN);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
}

test(`${NODE_COUNT} nodes and ${EDGE_COUNT} edges keep drag and React commit p95 below 100 ms`, async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120_000);
  await page.addInitScript(() => {
    window.__AGENTCANVAS_LONG_TASKS__ = [];
    if ("PerformanceObserver" in window) {
      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            window.__AGENTCANVAS_LONG_TASKS__?.push(entry.duration);
          }
        }).observe({ type: "longtask", buffered: true });
      } catch {
        // Browser does not expose long-task entries; drag/commit gates still apply.
      }
    }
  });

  const created = await createWorkflow(request);
  const workflowId = created.id;
  expect(created.nodeCount).toBe(NODE_COUNT);
  expect(created.edgeCount).toBe(EDGE_COUNT);
  try {
    await page.goto(`/workflows/${workflowId}?perf=1`);
    await login(page);
    const renderedNodes = page.locator(".react-flow__node");
    await expect(renderedNodes.first()).toBeVisible({ timeout: 20_000 });
    // React Flow virtualizes off-screen nodes and edges. The API assertions
    // prove the requested scenario while these checks prove the canvas rendered.
    expect(await renderedNodes.count()).toBeGreaterThan(0);
    expect(await renderedNodes.count()).toBeLessThanOrEqual(NODE_COUNT);
    await expect(page.locator(".react-flow__edge").first()).toBeVisible();
    const target = page.locator(".react-flow__node:visible").nth(5);
    await expect(target).toBeVisible();
    // Selecting an Agent mounts the Inspector and can discover MCP tools. Let
    // that separate interaction settle before measuring steady-state dragging.
    await target.click();
    await page.waitForLoadState("networkidle");
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await page.evaluate(() => {
      window.__AGENTCANVAS_DRAG_PAINTS__ = [];
      window.addEventListener(
        "pointermove",
        (event) => {
          if (event.buttons !== 1 || !window.__AGENTCANVAS_DRAG_PAINTS__) return;
          const samples = window.__AGENTCANVAS_DRAG_PAINTS__;
          const started = performance.now();
          requestAnimationFrame(() =>
            requestAnimationFrame(() => samples.push(performance.now() - started)),
          );
        },
        { passive: true },
      );
    });
    const box = await target.boundingBox();
    expect(box).not.toBeNull();
    const centerX = box!.x + box!.width / 2;
    const centerY = box!.y + box!.height / 2;
    await page.mouse.move(centerX, centerY);
    await page.mouse.down();

    // Exclude one-time browser layout/JIT work from the steady-state drag SLO.
    const warmupSamples = 12;
    for (let index = 0; index < warmupSamples; index += 1) {
      await page.mouse.move(centerX + (index + 1) * 2, centerY + index, { steps: 1 });
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
    await page.evaluate(() => {
      window.__AGENTCANVAS_DRAG_PAINTS__ = [];
      window.__AGENTCANVAS_PERF__ = { commits: [] };
      window.__AGENTCANVAS_LONG_TASKS__ = [];
    });

    for (let index = 0; index < 20; index += 1) {
      await page.mouse.move(
        centerX + (warmupSamples + index + 1) * 2,
        centerY + warmupSamples + index,
        { steps: 1 },
      );
      await page.waitForFunction(
        (expectedSamples) =>
          (window.__AGENTCANVAS_DRAG_PAINTS__?.length ?? 0) >= expectedSamples,
        index + 1,
      );
    }
    await page.mouse.up();

    const browserMetrics = await page.evaluate(() => ({
      dragPaints: window.__AGENTCANVAS_DRAG_PAINTS__ ?? [],
      commits: window.__AGENTCANVAS_PERF__?.commits ?? [],
      longTasks: window.__AGENTCANVAS_LONG_TASKS__ ?? [],
      domNodes: document.getElementsByTagName("*").length,
      heapBytes:
        "memory" in performance
          ? (performance as Performance & { memory: { usedJSHeapSize: number } }).memory
              .usedJSHeapSize
          : null,
    }));
    const report = {
      scenario: { nodes: NODE_COUNT, edges: EDGE_COUNT, dragSamples: browserMetrics.dragPaints.length },
      dragPaintP95Ms: percentile(browserMetrics.dragPaints, 0.95),
      dragPaintSamplesMs: browserMetrics.dragPaints,
      reactCommitP95Ms: percentile(browserMetrics.commits, 0.95),
      reactCommitSamplesMs: browserMetrics.commits,
      maxLongTaskMs: Math.max(0, ...browserMetrics.longTasks),
      domNodes: browserMetrics.domNodes,
      heapBytes: browserMetrics.heapBytes,
    };
    console.log("canvas performance", JSON.stringify(report));
    await testInfo.attach("canvas-performance.json", {
      body: JSON.stringify(report, null, 2),
      contentType: "application/json",
    });

    expect(browserMetrics.commits.length).toBeGreaterThan(0);
    expect(report.dragPaintP95Ms).toBeLessThan(100);
    expect(report.reactCommitP95Ms).toBeLessThan(100);
  } finally {
    await request.delete(`${API_URL}/api/workflows/${workflowId}`, { headers: adminHeaders });
  }
});
