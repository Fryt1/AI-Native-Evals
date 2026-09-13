import { cp, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "..", "dist");
const target = resolve(here, "..", "..", "..", "src", "ai_native_evals_console", "static");

await rm(target, { recursive: true, force: true });
await cp(source, target, { recursive: true });
console.log(`Synced Console static assets to ${target}`);
