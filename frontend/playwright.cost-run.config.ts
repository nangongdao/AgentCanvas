import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Temporary: run the locally-scoped cost suite (ignored by the default
// config) to verify the C5-11 axe addition.
export default defineConfig({
  ...(base as Record<string, unknown>),
  testIgnore: undefined,
});
