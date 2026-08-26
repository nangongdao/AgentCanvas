import { readFile, readdir, stat } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import path from "node:path";

const distDir = path.resolve("dist");
const initialGzipLimit = Number(process.env.BUNDLE_INITIAL_GZIP_KIB ?? 120) * 1024;
const chunkRawLimit = Number(process.env.BUNDLE_CHUNK_RAW_KIB ?? 350) * 1024;
// C6-5: the standalone app runtime page (C3-2) must stay its own small chunk
// so terminal-user loads never pull in platform code. Missing chunk = fail.
const runtimeGzipLimit = Number(process.env.BUNDLE_RUNTIME_GZIP_KIB ?? 40) * 1024;

async function listJavaScript(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listJavaScript(target)));
    else if (entry.name.endsWith(".js")) files.push(target);
  }
  return files;
}

function assetPaths(html) {
  const matches = html.matchAll(
    /<(?:script|link)\b[^>]*(?:src|href)=["']([^"']+\.js)["'][^>]*>/g,
  );
  return [...new Set([...matches].map((match) => match[1].replace(/^\//, "")))];
}

function kib(bytes) {
  return `${(bytes / 1024).toFixed(2)} KiB`;
}

const html = await readFile(path.join(distDir, "index.html"), "utf8");
const initialAssets = assetPaths(html);
if (initialAssets.length === 0) {
  throw new Error("bundle budget: dist/index.html has no initial JavaScript assets");
}

let initialGzip = 0;
for (const asset of initialAssets) {
  const contents = await readFile(path.join(distDir, asset));
  initialGzip += gzipSync(contents).byteLength;
}

const oversized = [];
for (const file of await listJavaScript(distDir)) {
  const size = (await stat(file)).size;
  if (size >= chunkRawLimit) oversized.push(`${path.relative(distDir, file)} (${kib(size)})`);
}

const runtimeChunk = (
  await listJavaScript(distDir)
).find((file) => path.basename(file).startsWith("AppRuntimePage-"));
let runtimeGzip = null;
if (runtimeChunk) {
  runtimeGzip = gzipSync(await readFile(runtimeChunk)).byteLength;
}
console.log(
  `bundle budget: initial gzip ${kib(initialGzip)} / < ${kib(initialGzipLimit)}; ` +
    `largest raw chunk limit < ${kib(chunkRawLimit)}; ` +
    `runtime page ${runtimeChunk ? kib(runtimeGzip) : "MISSING"} / < ${kib(runtimeGzipLimit)}`,
);
if (!runtimeChunk || runtimeGzip >= runtimeGzipLimit) {
  throw new Error(
    !runtimeChunk
      ? "AppRuntimePage chunk missing — the terminal-user runtime page must stay a separate lazy entry"
      : `runtime page JavaScript exceeds budget: ${kib(runtimeGzip)} >= ${kib(runtimeGzipLimit)}`,
  );
}
if (initialGzip >= initialGzipLimit) {
  throw new Error(
    `initial JavaScript exceeds budget: ${kib(initialGzip)} >= ${kib(initialGzipLimit)}\n` +
      initialAssets.join("\n"),
  );
}
if (oversized.length > 0) {
  throw new Error(`JavaScript chunks exceed raw budget:\n${oversized.join("\n")}`);
}
