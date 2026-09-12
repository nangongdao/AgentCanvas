#!/usr/bin/env node
/**
 * Installer size gate (C9 §7.3): fails when the packaged NSIS installer
 * exceeds the hard cap, and warns at the target.
 *
 *   node scripts/check-installer-size.mjs <path-to-installer> [--target-mb 300] [--cap-mb 450]
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const args = process.argv.slice(2);

function option(name, fallback) {
  const index = args.indexOf(name);
  return index !== -1 ? Number(args[index + 1]) : fallback;
}

const targetMb = option("--target-mb", 300);
const capMb = option("--cap-mb", 450);
const dir = args.find((arg) => !arg.startsWith("--"));

function fail(message) {
  console.error(`installer size gate: FAILED — ${message}`);
  process.exit(1);
}

if (!dir) fail("usage: check-installer-size.mjs <bundle-dir> [--target-mb 300] [--cap-mb 450]");
if (!existsSync(dir)) fail(`bundle dir not found: ${dir}`);

const installers = readdirSync(dir)
  .filter((name) => name.endsWith(".exe") || name.endsWith(".msi"))
  .map((name) => {
    const file = path.join(dir, name);
    return { name, bytes: statSync(file).size };
  })
  .sort((a, b) => b.bytes - a.bytes);

if (installers.length === 0) fail(`no installer found in ${dir}`);

for (const { name, bytes } of installers) {
  const mib = bytes / 2 ** 20;
  const label = `${name}: ${mib.toFixed(1)} MiB`;
  if (mib > capMb) fail(`${label} exceeds the ${capMb} MiB cap`);
  if (mib > targetMb) {
    console.warn(`installer size gate: WARN — ${label} exceeds the ${targetMb} MiB target (cap ${capMb} MiB)`);
  } else {
    console.log(`installer size gate: ${label} (target < ${targetMb} MiB, cap < ${capMb} MiB)`);
  }
}
