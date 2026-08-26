import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const schemaUrl = new URL("../../contracts/current/openapi.json", import.meta.url);
const outputUrl = new URL("../src/api/generated/openapi.ts", import.meta.url);
const ast = await openapiTS(schemaUrl, {
  alphabetize: true,
  immutable: true,
});
const generated = `${COMMENT_HEADER}${astToString(ast)}`;
const outputPath = fileURLToPath(outputUrl);

if (process.argv.includes("--write")) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
} else {
  const current = await readFile(outputPath, "utf8").catch(() => "");
  if (current !== generated) {
    console.error("generated OpenAPI types are stale; run pnpm contracts:generate");
    process.exitCode = 1;
  }
}
