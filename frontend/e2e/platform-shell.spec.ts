import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login } from "./support";

async function openAsAdmin(page: Page, path: string) {
  await page.goto(path);
  await expect(page.locator("#auth-dialog-title")).toBeVisible();
  await login(page, "admin");
}

function overviewPayload(projectId: string | null, total: number) {
  return {
    project_id: projectId,
    days: 7,
    since: "2026-08-13T00:00:00Z",
    recent_workflows: [],
    execution_summary: {
      total,
      succeeded: total,
      failed: 0,
      active: 0,
      cancelled: 0,
      success_rate: 1,
    },
    estimated_cost_usd: "0.000000000000",
    cost_known: true,
    daily: Array.from({ length: 7 }, (_, offset) => ({
      date: `2026-08-${String(13 + offset).padStart(2, "0")}`,
      executions: offset === 6 ? total : 0,
      succeeded: offset === 6 ? total : 0,
      failed: 0,
      estimated_cost_usd: "0.000000000000",
      cost_known: true,
    })),
  };
}

function quotaPayload(projectId: string, stdioLimit: number | null) {
  return {
    project_id: projectId,
    period_start: "2026-08-01T00:00:00Z",
    can_update: true,
    concurrent_execution_limit: null,
    storage_bytes_limit: null,
    monthly_embedding_input_bytes_limit: null,
    monthly_model_cost_usd_limit: null,
    stdio_mcp_process_limit: stdioLimit,
    concurrent_executions: 0,
    storage_bytes: 0,
    embedding_input_bytes: 0,
    model_cost_usd: "0.000000000000",
    stdio_mcp_processes: 0,
    concurrent_executions_remaining: null,
    storage_bytes_remaining: null,
    embedding_input_bytes_remaining: null,
    model_cost_usd_remaining: null,
    stdio_mcp_processes_remaining: stdioLimit,
  };
}

test("overview is the authenticated home inside the persistent platform shell", async ({
  page,
}, testInfo) => {
  await openAsAdmin(page, "/");

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "工作台概览" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("link", { name: "工作流", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "知识库" })).toBeVisible();
  await expect(page.getByRole("link", { name: "模型与供应商" })).toBeVisible();
  await expect(page.getByLabel("概览项目范围")).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) =>
      ["critical", "serious"].includes(violation.impact ?? ""),
    ),
  ).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("overview-desktop.png") });
});

test("legacy settings deep links preserve compatibility", async ({ page }) => {
  await openAsAdmin(page, "/models");

  await expect(page).toHaveURL(/\/settings\/models$/);
  await expect(page.getByRole("heading", { name: "AgentCanvas Models" })).toBeVisible();
  await expect(page.getByRole("link", { name: "模型与供应商" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("canonical settings pages remain reachable and pass serious axe checks", async ({ page }) => {
  await openAsAdmin(page, "/");
  const destinations = [
    ["模型与供应商", /\/settings\/models$/, "AgentCanvas Models"],
    ["MCP", /\/settings\/mcp$/, "MCP Catalog Control"],
    ["项目配额", /\/settings\/quotas$/, "项目配额"],
    ["审计日志", /\/settings\/audit$/, "审计日志"],
  ] as const;

  for (const [linkName, url, heading] of destinations) {
    await page.getByRole("link", { name: linkName, exact: true }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(
      accessibility.violations.filter((violation) =>
        ["critical", "serious"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);
  }
});

test("project switching ignores stale responses and treats zero quota as exhausted", async ({
  page,
}) => {
  await openAsAdmin(page, "/");
  let releaseProjectA = () => {};
  let markProjectAStarted = () => {};
  const projectAHold = new Promise<void>((resolve) => {
    releaseProjectA = resolve;
  });
  const projectAStarted = new Promise<void>((resolve) => {
    markProjectAStarted = resolve;
  });
  let projectListRequests = 0;
  const overviewRequests = new Map<string, number>();
  await page.route("**/api/projects", (route) => {
    projectListRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "project-a", organization_id: "org", name: "Project A", slug: "a" },
        { id: "project-b", organization_id: "org", name: "Project B", slug: "b" },
      ]),
    });
  });
  await page.route("**/api/overview?*", async (route) => {
    const projectId = new URL(route.request().url()).searchParams.get("project_id");
    const requestKey = projectId ?? "unscoped";
    overviewRequests.set(requestKey, (overviewRequests.get(requestKey) ?? 0) + 1);
    if (projectId === "project-a") {
      markProjectAStarted();
      await projectAHold;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overviewPayload(projectId, projectId === "project-b" ? 22 : 11)),
    });
  });
  await page.route("**/api/projects/*/quotas", (route) => {
    const projectId = new URL(route.request().url()).pathname.split("/")[3] ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(quotaPayload(projectId, projectId === "project-b" ? 0 : null)),
    });
  });

  await page.reload();
  await projectAStarted;
  await page.waitForTimeout(100);
  const discoveryRequests = projectListRequests;
  await page.getByLabel("概览项目范围").selectOption("project-b");
  const executionsMetric = page.getByText("近 7 日执行", { exact: true }).locator("../..");
  await expect(executionsMetric.getByText("22", { exact: true })).toBeVisible();
  const quotaMetric = page.getByText("配额水位", { exact: true }).first().locator("../..");
  await expect(quotaMetric.getByText("100%", { exact: true })).toBeVisible();
  expect(projectListRequests).toBe(discoveryRequests);
  expect(overviewRequests.get("project-b")).toBe(1);

  const projectAResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/overview" && url.searchParams.get("project_id") === "project-a";
  });
  releaseProjectA();
  await projectAResponse;
  await page.waitForTimeout(100);
  await expect(executionsMetric.getByText("22", { exact: true })).toBeVisible();
});

test("overview keeps quota failures distinct from an empty project state", async ({ page }) => {
  await openAsAdmin(page, "/");
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "project-a", organization_id: "org", name: "Project A", slug: "a" },
      ]),
    }),
  );
  await page.route("**/api/overview?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overviewPayload("project-a", 7)),
    }),
  );
  await page.route("**/api/projects/*/quotas", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "配额服务暂不可用" }),
    }),
  );

  await page.reload();
  await expect(page.getByText("配额服务暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("配额数据不可用", { exact: true })).toBeVisible();
  const quotaMetric = page.getByText("配额水位", { exact: true }).first().locator("../..");
  await expect(quotaMetric.getByText("不可用", { exact: true })).toBeVisible();
  await expect(quotaMetric.getByText("配额服务请求失败", { exact: true })).toBeVisible();
  const executionsMetric = page.getByText("近 7 日执行", { exact: true }).locator("../..");
  await expect(executionsMetric.getByText("7", { exact: true })).toBeVisible();
});

test("mobile navigation is operable without viewport overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openAsAdmin(page, "/");

  await expect(page.getByRole("navigation", { name: "主导航" })).toBeHidden();
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("link", { name: "工作流", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭导航", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeHidden();
  await expect(page.getByRole("button", { name: "打开导航" })).toBeFocused();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("overview-mobile.png") });
});
