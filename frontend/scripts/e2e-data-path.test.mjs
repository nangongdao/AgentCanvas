import { existsSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

import { createE2eDataPath } from "./e2e-data-path.mjs";

test("each E2E run gets an atomically-created isolated data directory", () => {
  const first = createE2eDataPath("agentcanvas-e2e");
  const second = createE2eDataPath("agentcanvas-e2e");
  try {
    assert.notEqual(first, second);
    assert.equal(existsSync(first), true);
    assert.equal(existsSync(second), true);
    writeFileSync(path.join(first, "stale-marker"), "old run", "utf8");
    assert.equal(existsSync(path.join(second, "stale-marker")), false);
  } finally {
    rmSync(first, { recursive: true, force: true });
    rmSync(second, { recursive: true, force: true });
  }
});
