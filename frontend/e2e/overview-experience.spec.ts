import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { OverviewDTO } from "../src/api/endpoints/overview";
import type { ProjectDTO, ProjectQuotaDTO } from "../src/api/endpoints/projectQuotas";
import { cleanupWorkflow, createWorkflow, login, type Role } from "./support";

// Synthetic, deterministic UI fixtures. These are not production telemetry.
function overviewPayload(extraExecutions = 0): OverviewDTO {
  const executions = [12, 23, 5, 8, 54, 30, 21 + extraExecutions];
  const costs = ["0.12", "0.23", null, "0.08", "0.54", "0.30", "0.21"];
  return {
    project_id: "project-ui",
    days: 7,
    since: "2026-09-02T00:00:00Z",
    estimated_cost_usd: null,
    cost_known: false,
    execution_summary: {
      total: 153 + extraExecutions,
      succeeded: 150 + extraExecutions,
      failed: 3,
      active: 0,
      cancelled: 0,
      success_rate: (150 + extraExecutions) / (153 + extraExecutions),
    },
    daily: executions.map((count, index) => ({
      date: `2026-09-${String(index + 2).padStart(2, "0")}`,
      executions: count,
      succeeded: count - (index === 2 ? 1 : index === 4 ? 2 : 0),
      failed: index === 2 ? 1 : index === 4 ? 2 : 0,
      // A cost marked unknown must not affect scale, even with a partial amount.
      estimated_cost_usd: costs[index] ?? "9999",
      cost_known: costs[index] !== null,
    })),
    recent_workflows: [
      { id: "fixture-release", name: "Release assistant", description: "Validate and prepare a release", project_id: "project-ui", updated_at: "2026-09-08T08:00:00Z" },
      { id: "fixture-knowledge", name: "Knowledge retrieval", description: "Find and cite approved context", project_id: "project-ui", updated_at: "2026-09-07T08:00:00Z" },
      { id: "fixture-support", name: "Support triage", description: "Route requests to the right workflow", project_id: "project-ui", updated_at: "2026-09-06T08:00:00Z" },
    ],
  };
}

function quotaPayload(): ProjectQuotaDTO {
  return {
    project_id: "project-ui",
    period_start: "2026-09-01T00:00:00Z",
    can_update: true,
    concurrent_execution_limit: 5,
    storage_bytes_limit: 1073741824,
    monthly_embedding_input_bytes_limit: 8388608,
    monthly_model_cost_usd_limit: "100",
    stdio_mcp_process_limit: 4,
    concurrent_executions: 2,
    storage_bytes: 402653184,
    embedding_input_bytes: 1572864,
    model_cost_usd: "14.36",
    stdio_mcp_processes: 1,
    concurrent_executions_remaining: 3,
    storage_bytes_remaining: 671088640,
    embedding_input_bytes_remaining: 6815744,
    model_cost_usd_remaining: "85.64",
    stdio_mcp_processes_remaining: 3,
  };
}

interface FixtureState {
  overview: OverviewDTO;
  quota: ProjectQuotaDTO;
  projects: ProjectDTO[];
  failProjects: boolean;
  failOverview: boolean;
  overviewWait: Promise<void> | null;
  requests: { projects: number; overview: number };
}

async function mockOverview(page: Page): Promise<FixtureState> {
  const state: FixtureState = {
    overview: overviewPayload(),
    quota: quotaPayload(),
    projects: [{ id: "project-ui", organization_id: "fixture-org", name: "Project Atlas", slug: "atlas" }],
    failProjects: false,
    failOverview: false,
    overviewWait: null,
    requests: { projects: 0, overview: 0 },
  };
  await page.route("**/api/projects", (route) => {
    state.requests.projects += 1;
    return route.fulfill({
      status: state.failProjects ? 503 : 200,
      json: state.failProjects ? { detail: "Project discovery fixture unavailable" } : state.projects,
    });
  });
  await page.route("**/api/overview?*", async (route) => {
    state.requests.overview += 1;
    if (state.overviewWait) await state.overviewWait;
    await route.fulfill({
      status: state.failOverview ? 503 : 200,
      json: state.failOverview ? { detail: "Overview fixture unavailable" } : state.overview,
    });
  });
  await page.route("**/api/projects/*/quotas", (route) => route.fulfill({ json: state.quota }));
  return state;
}

async function openOverview(page: Page, role: Role = "admin", path = "/") {
  await page.goto(path);
  await login(page, role);
  await expect(page.getByRole("heading", { name: "工作台概览" })).toBeVisible();
  await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
}

async function expectNoSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((issue) => ["serious", "critical"].includes(issue.impact ?? ""))).toEqual([]);
}

for (const theme of ["light", "dark"] as const) {
  test(`overview ${theme}: cost inspection is keyboard accessible and unpriced amounts do not set the scale`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript((value) => localStorage.setItem("agentcanvas:theme", value), theme);
    await mockOverview(page);
    await openOverview(page);
    await expect(page.locator("html")).toHaveCSS("color-scheme", theme);
    const chart = page.getByRole("group", { name: "费用趋势", exact: true });
    const detail = page.getByTestId("cost-day-detail");
    const unknownDay = chart.getByRole("button", { name: /^2026-09-04/ });
    await unknownDay.focus();
    await expect(unknownDay).toHaveAttribute("aria-pressed", "true");
    await expect(detail).toContainText("待定价");
    await expect(detail).toContainText("5 次执行");
    const largestKnownBar = chart.getByRole("button", { name: /^2026-09-06/ }).locator(".overview-chart-bar");
    expect(await largestKnownBar.evaluate((element) => element instanceof HTMLElement ? element.style.height : null)).toBe("100%");

    // An unpriced day's bar height is a placeholder, and the hatch is the only
    // cue that says so. It is carried by a background-image, so a later material
    // rule with higher specificity can shadow it and quietly render the day as
    // an ordinary priced bar — hence the assertion in both themes.
    expect(
      await unknownDay.locator(".overview-chart-bar").evaluate((element) => getComputedStyle(element).backgroundImage),
    ).toContain("repeating-linear-gradient");
    expect(
      await largestKnownBar.evaluate((element) => getComputedStyle(element).backgroundImage),
    ).not.toContain("repeating-linear-gradient");

    await unknownDay.press("Home");
    const firstDay = chart.getByRole("button", { name: /^2026-09-02/ });
    await expect(firstDay).toBeFocused();
    await expect(detail).toContainText("12 次执行");
    await firstDay.press("End");
    const lastDay = chart.getByRole("button", { name: /^2026-09-08/ });
    await expect(lastDay).toBeFocused();
    await lastDay.press("ArrowLeft");
    await expect(chart.getByRole("button", { name: /^2026-09-07/ })).toBeFocused();
    await expect(detail).toContainText("30 次执行");
    await firstDay.hover();
    await expect(detail).toContainText("12 次执行");
    await lastDay.hover();
    await expect(lastDay).toHaveAttribute("tabindex", "0");
    await expect(chart.locator('button[tabindex="0"]')).toHaveCount(1);
    await expect(page.getByText(/platform ready|control plane online/i)).toHaveCount(0);
    await expectNoSeriousAxe(page);
    await page.screenshot({ path: testInfo.outputPath(`overview-${theme}.png`) });
  });
}

test("overview refresh keeps current project data visible until the new response arrives", async ({ page }) => {
  const fixture = await mockOverview(page);
  await openOverview(page);
  const metric = page.locator('[data-metric="executions"]');
  await expect(metric.getByText("153", { exact: true })).toBeVisible();
  let release!: () => void;
  fixture.overviewWait = new Promise<void>((resolve) => { release = resolve; });
  try {
    await page.getByRole("button", { name: "刷新概览", exact: true }).click();
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "true");
    await expect(page.getByText("正在更新…", { exact: true })).toBeVisible();
    await expect(metric.getByText("153", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新概览", exact: true })).toBeDisabled();
    fixture.overview = overviewPayload(1);
    release();
    await expect(metric.getByText("154", { exact: true })).toBeVisible();
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
  } finally {
    release();
  }
});

test("failed overview data is unknown, not zero or an empty workflow list, and retry recovers", async ({ page }) => {
  const fixture = await mockOverview(page);
  fixture.failOverview = true;
  await openOverview(page);
  await expect(page.getByRole("alert")).toContainText("Overview fixture unavailable");
  const metric = page.locator('[data-metric="executions"]');
  await expect(metric.getByText("—", { exact: true })).toBeVisible();
  await expect(metric.getByText("0", { exact: true })).toHaveCount(0);
  await expect(page.getByText("暂无工作流", { exact: true })).toHaveCount(0);
  await expect(page.getByText("暂无费用数据", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "运营数据不可用" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "存储配额占用" })).toBeVisible();
  const discoveryCount = fixture.requests.projects;
  fixture.failOverview = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(metric.getByText("153", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(fixture.requests.projects).toBe(discoveryCount);
});

test("failed project discovery preserves URL scope and retries discovery rather than fetching unscoped data", async ({ page }) => {
  const fixture = await mockOverview(page);
  fixture.failProjects = true;
  await openOverview(page, "admin", "/?project_id=project-ui&filter=keep");
  await expect(page.getByRole("alert")).toContainText("Project discovery fixture unavailable");
  expect(new URL(page.url()).searchParams.get("project_id")).toBe("project-ui");
  expect(new URL(page.url()).searchParams.get("filter")).toBe("keep");
  expect(fixture.requests.overview).toBe(0);
  await expect(page.getByLabel("概览项目范围")).toBeDisabled();
  fixture.failProjects = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.locator('[data-metric="executions"]').getByText("153", { exact: true })).toBeVisible();
  await expect(page.getByLabel("概览项目范围")).toHaveValue("project-ui");
  expect(new URL(page.url()).searchParams.get("filter")).toBe("keep");
  expect(fixture.requests.overview).toBe(1);
});

test("a successful empty overview displays genuine zero counts and actionable empty states", async ({ page }) => {
  const fixture = await mockOverview(page);
  fixture.overview = {
    ...fixture.overview,
    daily: [],
    recent_workflows: [],
    cost_known: true,
    estimated_cost_usd: "0",
    execution_summary: { total: 0, succeeded: 0, failed: 0, active: 0, cancelled: 0, success_rate: null },
  };
  await openOverview(page);
  await expect(page.locator('[data-metric="executions"]').getByText("0", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无费用数据", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无工作流", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "费用趋势", exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("overview copy follows the account language without refetching and persists on reload", async ({ page }) => {
  const fixture = await mockOverview(page);
  await openOverview(page);
  const requestsBefore = fixture.requests.overview;
  await page.locator('button[title="当前访问角色与偏好"]').click();
  await page.getByRole("radiogroup", { name: "语言", exact: true }).getByRole("radio", { name: "English", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Workspace overview", exact: true })).toBeVisible();
  await expect(page.getByLabel("Overview project scope")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cost trend", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Collapse sidebar", exact: true })).toBeVisible();
  await expect(page.locator('[data-metric="executions"]').getByText("153", { exact: true })).toBeVisible();
  expect(fixture.requests.overview).toBe(requestsBefore);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Workspace overview", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: /^Recent workflows/ })).toBeVisible();
});

test("compact navigation keeps role restrictions and accessible link names", async ({ page }) => {
  await mockOverview(page);
  await openOverview(page, "viewer");
  const navigation = page.getByRole("navigation", { name: "主导航" });
  await expect(navigation.getByRole("link", { name: "MCP", exact: true })).toHaveCount(0);
  await expect(navigation.getByRole("link", { name: "审计日志", exact: true })).toHaveCount(0);
  await expect(navigation.getByRole("link", { name: "平台管理", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "收起侧边栏", exact: true }).click();
  await expect(page.getByTestId("desktop-navigation")).toHaveCSS("width", "72px");
  await expect(navigation.getByRole("link", { name: "工作流", exact: true })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "概览", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("link", { name: "MCP", exact: true })).toHaveCount(0);
});

test("saved workflow routes share active navigation and compact layout survives reload", async ({ page, request }) => {
  const workflow = await createWorkflow(request, `UI navigation ${Date.now()}`);
  let completed = false;
  try {
    await page.goto(`/workflows/${workflow.id}`);
    await login(page, "admin");
    await expect(page.locator(".react-flow")).toBeVisible();
    const workflowLink = page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "工作流", exact: true });
    await expect(workflowLink).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("workspace-destination")).toHaveText("工作流");
    await page.getByRole("button", { name: "收起侧边栏", exact: true }).click();
    await expect(page.getByTestId("desktop-navigation")).toHaveCSS("width", "72px");
    await page.reload();
    await expect(page.locator(".react-flow")).toBeVisible();
    await expect(page.getByTestId("desktop-navigation")).toHaveCSS("width", "72px");
    await expect(workflowLink).toHaveAttribute("aria-current", "page");
    await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
    await expect(page.getByTestId("desktop-navigation")).toHaveCSS("width", "224px");
    completed = true;
  } finally {
    await cleanupWorkflow(request, workflow.id, completed);
  }
});

test.describe("mobile overview", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("touch inspection, drawer focus, desktop resize, and reduced motion remain usable", async ({ page }, testInfo) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await mockOverview(page);
    await openOverview(page);
    await page.screenshot({ path: testInfo.outputPath("overview-mobile.png") });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    await expect(page.locator(".overview-panel").first()).toHaveCSS("animation-name", "none");
    const openButton = page.getByRole("button", { name: "打开导航", exact: true });
    await openButton.tap();
    const drawer = page.getByRole("dialog", { name: "导航菜单", exact: true });
    const closeButton = drawer.getByRole("button", { name: "关闭导航", exact: true });
    await expect(closeButton).toBeFocused();
    await closeButton.press("Shift+Tab");
    await expect(drawer.getByRole("link").last()).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(closeButton).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(openButton).toBeFocused();

    const unknownDay = page.getByRole("group", { name: "费用趋势", exact: true }).getByRole("button", { name: /^2026-09-04/ });
    await unknownDay.tap();
    await expect(page.getByTestId("cost-day-detail")).toContainText("待定价");
    await expect(unknownDay).toHaveAttribute("aria-pressed", "true");
    await expectNoSeriousAxe(page);
    await page.screenshot({ path: testInfo.outputPath("overview-mobile-chart.png") });

    await openButton.tap();
    await expect(drawer).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(drawer).toHaveCount(0);
    await expect(page.locator(".platform-shell [inert]")).toHaveCount(0);
    await expect(page.locator("#main-content")).toBeFocused();
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  });

  test("long English metric states wrap instead of clipping", async ({ page }) => {
    const fixture = await mockOverview(page);
    fixture.quota = {
      ...fixture.quota,
      concurrent_execution_limit: null,
      storage_bytes_limit: null,
      monthly_embedding_input_bytes_limit: null,
      monthly_model_cost_usd_limit: null,
      stdio_mcp_process_limit: null,
    };

    await page.goto("/");
    await login(page, "admin");
    await page.evaluate(() => localStorage.setItem("agentcanvas:locale", "en"));
    await page.reload();
    await expect(page.getByRole("heading", { name: "Workspace overview", exact: true })).toBeVisible();
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
    const value = page.locator('[data-metric="quota"]').getByText("Not configured", { exact: true });
    await expect(value).toBeVisible();
    expect(await value.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  });
});
