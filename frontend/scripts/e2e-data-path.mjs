import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

export function createE2eDataPath(prefix) {
  return mkdtempSync(path.join(tmpdir(), `${prefix}-`));
}
