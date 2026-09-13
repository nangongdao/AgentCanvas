import { test, expect } from "@playwright/test";
import {
  createWorkflow,
  cleanupWorkflow,
  captureUnexpectedErrors,
  login,
  API_URL,
  adminHeaders,
} from "./support";

test("execution history exports to CSV with all records and correct format", async ({
  page,
  request,
}) => {
  const errors = captureUnexpectedErrors(page);
  const created = await createWorkflow(request, `Export Test ${Date.now()}`);

  try {
    // Create multiple executions via API
    const executionIds: string[] = [];
    for (let i = 0; i < 3; i++) {
      const execResponse = await request.post(
        `${API_URL}/api/workflows/${created.id}/run`,
        {
          headers: adminHeaders,
          data: { inputs: { user_query: `Test query ${i + 1}` } },
        }
      );
      expect(execResponse.status()).toBe(201);
      const execData = await execResponse.json();
      executionIds.push(execData.id);
    }

    // Wait for executions to complete
    await page.waitForTimeout(2000);

    // Navigate to the workflow
    await page.goto(`/workflows/${created.id}`);
    await login(page, "admin");
    await page.waitForLoadState("networkidle");

  // Open execution history drawer
  await page.getByRole("button", { name: "历史" }).click();
  await expect(page.getByText(/execution history/i)).toBeVisible();

  // Setup download listener
  const downloadPromise = page.waitForEvent("download");

  // Click export button
  await page.getByRole("button", { name: "导出CSV" }).click();

  // Wait for download
  const download = await downloadPromise;
  const filename = download.suggestedFilename();

  // Verify filename format: {workflow-name}-executions-{timestamp}.csv
  expect(filename).toMatch(/Export Test \d+-executions-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.csv/);

  // Read CSV content
  const path = await download.path();
  const fs = await import("fs");
  const csvContent = fs.readFileSync(path!, "utf-8");

  // Verify BOM for Excel UTF-8 support
  expect(csvContent.charCodeAt(0)).toBe(0xfeff);

  // Parse CSV
  const lines = csvContent.trim().split("\n");
  expect(lines.length).toBeGreaterThanOrEqual(4); // Header + at least 3 executions

  // Verify header
  const header = lines[0];
  expect(header).toContain("执行ID");
  expect(header).toContain("状态");
  expect(header).toContain("触发来源");
  expect(header).toContain("版本号");
  expect(header).toContain("开始时间");
  expect(header).toContain("结束时间");
  expect(header).toContain("持续时间(秒)");
  expect(header).toContain("错误信息");

  // Verify data rows
  for (let i = 1; i < lines.length; i++) {
    const row = lines[i];
    // Each row should have 8 columns (wrapped in quotes)
    const columns = row.split('","');
    expect(columns.length).toBe(8);

    // Verify execution ID format (hex string without dashes)
    const executionId = columns[0].replace(/^"|"$/g, "");
    expect(executionId).toMatch(/^[0-9a-f]{32}$/i);

    // Verify status is one of the valid values (lowercase as returned by API)
    const status = columns[1];
    expect(["queued", "running", "succeeded", "failed", "cancelled"]).toContain(
      status
    );

    // Verify trigger source
    const triggerSource = columns[2];
    expect(triggerSource).toBe("manual");

    // Verify timestamps are ISO format if present
    const startTime = columns[4];
    const endTime = columns[5];
    if (startTime) {
      expect(startTime).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    }
    if (endTime) {
      expect(endTime).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    }

    // Verify duration is a number if present
    const duration = columns[6];
    if (duration) {
      expect(parseFloat(duration)).toBeGreaterThan(0);
    }
  }

  } finally {
    await cleanupWorkflow(request, created.id);
    expect(errors.pageErrors).toEqual([]);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.httpErrors).toEqual([]);
  }
});
