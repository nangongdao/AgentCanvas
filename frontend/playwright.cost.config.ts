import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { createE2eDataPath } from "./scripts/e2e-data-path.mjs";

// Focused runner for the cost-governance E2E spec. Starts the backend with the
// project venv directly (no `uv` on PATH required) and reuses servers when they
// are already running so `pnpm test:e2e` and a manual smoke run can coexist.
const backendDir = path.resolve(process.cwd(), "../backend");
const backendPort = process.env.AGENTCANVAS_E2E_BACKEND_PORT ?? "8000";
const frontendPort = process.env.AGENTCANVAS_E2E_FRONTEND_PORT ?? "5173";
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const venvPython = path.join(backendDir, ".venv", "Scripts", "python.exe");
const dataPath = createE2eDataPath("agentcanvas-cost-e2e");
const databasePath = path.join(dataPath, "app.db").replaceAll("\\", "/");
const inheritedEnv = Object.fromEntries(
  Object.entries(process.env).filter(
    (entry): entry is [string, string] => entry[1] !== undefined,
  ),
);
const BACKEND_ENV = {
  ...inheritedEnv,
  APP_ENV: "test",
  AUTH_MODE: "token",
  ADMIN_API_TOKEN: "admin-e2e-token-20260729",
  EDITOR_API_TOKEN: "editor-e2e-token-20260729",
  VIEWER_API_TOKEN: "viewer-e2e-token-20260729",
  RATE_LIMIT_DEFAULT_REQUESTS: "1000",
  RATE_LIMIT_LOGIN_REQUESTS: "1000",
  SECRET_KEY: "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
  DATABASE_URL: `sqlite+aiosqlite:///${databasePath}`,
  APP_DATA_DIR: dataPath,
  OPENAI_API_KEY: "",
  MODEL_MAX_COST_USD_PER_EXECUTION: "0.000001",
  CORS_ORIGINS: frontendUrl,
  MCP_STDIO_ALLOWED_ROOTS: path.resolve(backendDir, "mcp_servers"),
  PYTHONUTF8: "1",
};

function findWindowsBrowser(): string | undefined {
  if (process.env.CI || process.platform !== "win32" || !process.env.LOCALAPPDATA) {
    return undefined;
  }
  const root = path.join(process.env.LOCALAPPDATA, "ms-playwright");
  if (existsSync(root)) {
    const candidates = readdirSync(root)
      .filter((name) => /^chromium-\d+$/.test(name))
      .sort((left, right) => Number(right.split("-")[1]) - Number(left.split("-")[1]));
    const cached = candidates
      .map((name) => path.join(root, name, "chrome-win64", "chrome.exe"))
      .find((candidate) => existsSync(candidate));
    if (cached) return cached;
  }
  return [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  ].find((candidate) => existsSync(candidate));
}

const localBrowser = findWindowsBrowser();

export default defineConfig({
  testDir: "./e2e",
  testMatch: /cost-governance\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["line"]],
  use: {
    baseURL: frontendUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: localBrowser ? { executablePath: localBrowser } : undefined,
      },
    },
  ],
  webServer: [
    {
      command: `"${venvPython}" -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: backendDir,
      url: `${backendUrl}/healthz`,
      timeout: 60_000,
      reuseExistingServer: true,
      env: BACKEND_ENV,
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${frontendPort}`,
      cwd: process.cwd(),
      url: `${frontendUrl}/healthz`,
      timeout: 60_000,
      reuseExistingServer: true,
      env: {
        ...inheritedEnv,
        VITE_API_PROXY_TARGET: backendUrl,
      },
    },
  ],
});
