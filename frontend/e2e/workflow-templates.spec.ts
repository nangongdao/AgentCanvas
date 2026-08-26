import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import {
  captureUnexpectedErrors,
  cleanupWorkflow,
  createWorkflow,
  login,
} from "./support";

test("official and team templates create searchable parameterized workflows", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = captureUnexpectedErrors(page);
  const suffix = Date.now();
  const source = await createWorkflow(request, `Template source ${suffix}`);
  let generatedId = "";
  let bodyCompleted = false;

  try {
    await page.goto(`/workflows/${source.id}`);
    await login(page, "editor");
    await page.locator('button[title="模板库"]').click();
    let gallery = page.getByRole("dialog", { name: "工作流模板" });
    await expect(gallery).toBeVisible();
    await expect(gallery.getByText("Demo · Linear Q&A", { exact: true })).toBeVisible();

    // C5-11: the template gallery dialog passes serious axe checks.
    const galleryAxe = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .analyze();
    expect(
      galleryAxe.violations.filter((v) =>
        ["critical", "serious"].includes(v.impact ?? ""),
      ),
    ).toEqual([]);

    const search = gallery.getByLabel("搜索模板");
    await search.fill("Linear");
    const linear = gallery.locator("article").filter({ hasText: "Demo · Linear Q&A" });
    await expect(linear).toBeVisible();
    await linear.getByRole("button", { name: "使用模板", exact: true }).click();

    await gallery.getByLabel("工作流名称").fill(`Generated template ${suffix}`);
    await gallery.getByLabel("System prompt").fill("Answer with one verified sentence.");
    const instantiateResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/workflow-templates/official-linear/instantiate"),
    );
    await gallery.getByRole("button", { name: "创建工作流", exact: true }).click();
    const generated = await instantiateResponse;
    expect(generated.status()).toBe(201);
    generatedId = ((await generated.json()) as { id: string }).id;
    await page.waitForURL(new RegExp(`/workflows/${generatedId}$`));
    await expect(page.locator('input[placeholder="工作流名称"]')).toHaveValue(
      `Generated template ${suffix}`,
    );

    await page.locator('button[title="模板库"]').click();
    gallery = page.getByRole("dialog", { name: "工作流模板" });
    await gallery.getByRole("button", { name: "保存为模板", exact: true }).click();
    const teamName = `Team template ${suffix}`;
    await gallery.getByLabel("名称").fill(teamName);
    await gallery.getByLabel("分类").fill("team");
    await gallery.getByLabel("标签").fill("review, handoff");
    await gallery.getByLabel("描述").fill("Reusable release review flow");
    const createResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/workflow-templates"),
    );
    await gallery.getByRole("button", { name: "保存模板", exact: true }).click();
    const created = await createResponse;
    expect(created.status()).toBe(201);

    await gallery.getByLabel("搜索模板").fill(teamName);
    const teamCard = gallery.locator("article").filter({ hasText: teamName });
    await expect(teamCard).toBeVisible();
    await expect(teamCard.getByText("review", { exact: true })).toBeVisible();
    await expect(teamCard.getByText("handoff", { exact: true })).toBeVisible();

    page.once("dialog", (confirmation) => void confirmation.accept());
    await teamCard.locator('button[title="删除模板"]').click();
    await expect(teamCard).toHaveCount(0);

    await gallery.getByRole("button", { name: "关闭", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('button[title="模板库"]').click();
    gallery = page.getByRole("dialog", { name: "工作流模板" });
    await expect(gallery).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);

    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
    bodyCompleted = true;
  } finally {
    if (generatedId) await cleanupWorkflow(request, generatedId, bodyCompleted);
    await cleanupWorkflow(request, source.id, bodyCompleted);
  }
});
