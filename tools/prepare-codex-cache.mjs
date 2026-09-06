import fs from "node:fs/promises";
import path from "node:path";

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i];
  const value = process.argv[i + 1];
  if (!key?.startsWith("--") || value === undefined) {
    throw new Error("usage: prepare-codex-cache.mjs --version VERSION --registry URL --output-dir DIR");
  }
  args.set(key.slice(2), value);
}

const version = args.get("version") || "0.153.4";
const registry = (args.get("registry") || "https://registry.npmmirror.com").replace(/\/+$/, "");
const outputDir = path.resolve(args.get("output-dir") || "cache/codex");
const mainUrl = `${registry}/@openai/codex/-/codex-${version}.tgz`;
const linuxUrl = `${registry}/@openai/codex/-/codex-${version}-linux-x64.tgz`;
const mainFile = path.join(outputDir, "openai-codex.tgz");
const linuxFile = path.join(outputDir, "codex-linux-x64.tgz");
const versionFile = path.join(outputDir, "VERSION");

await fs.mkdir(outputDir, { recursive: true });
let cachedVersion = "";
try {
  cachedVersion = (await fs.readFile(versionFile, "utf8")).trim();
} catch {}
if (cachedVersion !== version) {
  await fs.rm(mainFile, { force: true });
  await fs.rm(linuxFile, { force: true });
}
await downloadWhole(mainUrl, mainFile);
await downloadRanges(linuxUrl, linuxFile);
await fs.writeFile(versionFile, `${version}\n`, "utf8");

async function request(url, init = {}, retries = 4) {
  let lastError;
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const response = await fetch(url, init);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response;
    } catch (error) {
      lastError = error;
      await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  throw lastError;
}

async function downloadWhole(url, output) {
  try {
    const stat = await fs.stat(output);
    if (stat.size > 0) {
      console.log(`cache hit: ${output}`);
      return;
    }
  } catch {}

  console.log(`downloading: ${url}`);
  const response = await request(url);
  const body = Buffer.from(await response.arrayBuffer());
  const temporary = `${output}.partial`;
  await fs.writeFile(temporary, body);
  await fs.rename(temporary, output);
  console.log(`saved ${body.length} bytes: ${output}`);
}

async function downloadRanges(url, output) {
  try {
    const stat = await fs.stat(output);
    if (stat.size > 1000000) {
      console.log(`cache hit: ${output}`);
      return;
    }
  } catch {}

  const head = await request(url, { method: "HEAD" });
  const total = Number(head.headers.get("content-length"));
  if (!Number.isFinite(total) || total <= 0) throw new Error(`no content-length for ${url}`);
  const chunkSize = 8 * 1024 * 1024;
  const chunks = [];
  for (let start = 0; start < total; start += chunkSize) {
    chunks.push({ start, end: Math.min(total - 1, start + chunkSize - 1) });
  }

  console.log(`downloading ${total} bytes in ${chunks.length} ranges: ${url}`);
  const temporary = `${output}.partial`;
  const handle = await fs.open(temporary, "w+");
  await handle.truncate(total);
  let next = 0;
  let completed = 0;
  const concurrency = 12;

  async function worker() {
    while (true) {
      const index = next++;
      if (index >= chunks.length) return;
      const { start, end } = chunks[index];
      const response = await request(url, { headers: { Range: `bytes=${start}-${end}` } });
      const range = response.headers.get("content-range") || "";
      if (!range.startsWith(`bytes ${start}-${end}/`)) {
        throw new Error(`unexpected content-range: ${range}`);
      }
      const body = Buffer.from(await response.arrayBuffer());
      if (body.length !== end - start + 1) throw new Error(`short range ${start}-${end}`);
      await handle.write(body, 0, body.length, start);
      completed += body.length;
      process.stdout.write(`\r${(completed / total * 100).toFixed(1)}%`);
    }
  }

  try {
    await Promise.all(Array.from({ length: Math.min(concurrency, chunks.length) }, worker));
    await handle.close();
    await fs.rename(temporary, output);
    console.log(`\nsaved ${total} bytes: ${output}`);
  } catch (error) {
    await handle.close();
    throw error;
  }
}
