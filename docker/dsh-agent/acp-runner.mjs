#!/usr/bin/env node
/**
 * In-container ACP client for the DSH Agent profile.
 *
 * The evaluator sends the task prompt as argv. This tiny process starts the
 * real DSH ACP server, sends standard initialize/session/new/session/prompt
 * requests, proxies incoming ACP notifications to stdout for the harness
 * trace, and closes the session. No DSH-private API is used.
 */

import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";

const prompt = process.argv.slice(2).join(" ");
if (!prompt) throw new Error("DSH ACP runner requires a task prompt");
const cwd = process.env.EVAL_WORKDIR || "/workspace";
const mcpPath = process.env.EVAL_DSH_MCP_SERVERS_FILE || "/run-config/dsh-mcp-servers.json";
let mcpServers = [];
try {
  mcpServers = JSON.parse(readFileSync(mcpPath, "utf8"));
} catch (error) {
  if (process.env.EVAL_MCP_SERVERS_FILE) {
    mcpServers = [];
  } else {
    throw new Error(`cannot read DSH MCP config: ${error.message}`);
  }
}

configureDshHome();

const dshExecutable = process.env.DSH_EXECUTABLE || "node";
let dshArgs = ["--import", "tsx/esm", "/opt/dsh/apps/cli/src/bin.ts", "--profile", "acp"];
if (process.env.DSH_SERVER_ARGS) {
  try {
    const parsed = JSON.parse(process.env.DSH_SERVER_ARGS);
    if (!Array.isArray(parsed) || !parsed.every((value) => typeof value === "string")) {
      throw new Error("DSH_SERVER_ARGS must be a JSON string array");
    }
    dshArgs = parsed;
  } catch (error) {
    throw new Error(`invalid DSH_SERVER_ARGS: ${error.message}`);
  }
}
const server = spawn(
  dshExecutable,
  dshArgs,
  {
    cwd: process.env.DSH_CWD || "/opt/dsh",
    env: process.env,
    stdio: ["pipe", "pipe", "pipe"],
  },
);
server.stderr.on("data", (chunk) => process.stderr.write(chunk));
const lines = createInterface({ input: server.stdout });
const iterator = lines[Symbol.asyncIterator]();
let nextId = 1;
let lastMessage = "";

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function writeRequest(method, params) {
  const id = nextId++;
  server.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  return id;
}

async function readMessage() {
  while (true) {
    const item = await iterator.next();
    if (item.done) throw new Error("DSH ACP server exited before responding");
    const line = item.value.trim();
    if (!line) continue;
    let value;
    try {
      value = JSON.parse(line);
    } catch {
      process.stderr.write(`${line}\n`);
      continue;
    }
    if (!value || typeof value !== "object") continue;
    send(value);
    const method = value.method;
    if (method === "session/update") collectUpdate(value);
    if (method === "session/request_permission") {
      answerPermission(value);
      continue;
    }
    return value;
  }
}

async function request(method, params) {
  const id = writeRequest(method, params);
  while (true) {
    const value = await readMessage();
    if (value.id !== id) continue;
    if (value.error) throw new Error(`DSH ACP ${method} failed: ${JSON.stringify(value.error)}`);
    return value.result && typeof value.result === "object" ? value.result : {};
  }
}

function collectUpdate(message) {
  const params = message.params && typeof message.params === "object" ? message.params : {};
  const update = params.update && typeof params.update === "object" ? params.update : params;
  if (update.sessionUpdate !== "agent_message_chunk") return;
  const content = update.content;
  if (content && typeof content === "object" && typeof content.text === "string") {
    lastMessage += content.text;
  }
}

function answerPermission(message) {
  const params = message.params && typeof message.params === "object" ? message.params : {};
  const options = Array.isArray(params.options) ? params.options : [];
  const configured = process.env.EVAL_PERMISSION_OPTION_ID || "allow-once";
  const available = options.find((option) => option && option.optionId === configured)
    || options.find((option) => option && typeof option.optionId === "string");
  server.stdin.write(`${JSON.stringify({
    jsonrpc: "2.0",
    id: message.id,
    result: { outcome: { outcome: "selected", optionId: available?.optionId || configured } },
  })}\n`);
}

function readAttachments() {
  // Files the evaluator delivered into the mounted configuration. This is the
  // DSH side of the contract: the framework says only "here is a directory",
  // and deciding that its contents are Cordis plugins is DSH's business, not
  // the evaluator's. A directory without a readable manifest is not a plugin;
  // skipping it keeps one malformed copy from failing the whole run.
  const root = process.env.EVAL_ATTACH_DIR || "/run-config/attach";
  if (!existsSync(root)) return [];
  const entries = [];
  for (const name of readdirSync(root)) {
    const dir = `${root}/${name}`;
    try {
      const manifest = JSON.parse(readFileSync(`${dir}/package.json`, "utf8"));
      const entry = manifest.exports?.["."]?.default || manifest.main;
      if (typeof entry === "string" && entry) entries.push({ name, path: `${dir}/${entry}` });
    } catch (error) {
      process.stderr.write(`ignoring attachment ${name}: ${error.message}\n`);
    }
  }
  return entries;
}

function configureDshHome() {
  const home = process.env.DSH_HOME || "/tmp/dsh-home";
  const model = process.env.EVAL_DSH_MODEL_ID || process.env.EVAL_MODEL || "deepseek-v4-flash";
  const gateway = process.env.EVAL_GATEWAY_URL || "http://llm-gateway:8080/v1";
  const reasoning = process.env.EVAL_REASONING_EFFORT || "high";
  mkdirSync(home, { recursive: true });
  // The release and source profiles both ship the built-in DeepSeek adapter.
  // Override only its endpoint/credential in the run-scoped home instead of
  // depending on the optional pi-ai composition package.
  writeFileSync(`${home}/settings.yaml`, `llm-deepseek:
  apiKeyEnv: EVAL_GATEWAY_API_KEY
  baseURL: ${JSON.stringify(gateway)}
  reasoningEffort: ${JSON.stringify(reasoning)}
`, "utf8");
  const systemPromptPath = process.env.EVAL_SYSTEM_PROMPT_FILE;
  const systemPrompt = systemPromptPath && existsSync(systemPromptPath)
    ? readFileSync(systemPromptPath, "utf8").trim()
    : "";
  const systemLayer = systemPrompt
    ? `- id: system-prompt\n  config:\n    persona: ${JSON.stringify(systemPrompt)}\n`
    : "";
  // The profile's user patch layer; the launcher loads this file itself, so an
  // attachment joins the composition without an image rebuild.
  const attachments = readAttachments();
  const attachLayer = attachments.length
    ? `- insert:\n${attachments
        .map(
          (item) =>
            `    - id: ${JSON.stringify(item.name)}\n      name: ${JSON.stringify(item.path)}\n`,
        )
        .join("")}`
    : "";
  if (attachments.length) {
    process.stderr.write(`composing ${attachments.length} attachment(s) from the run config\n`);
  }
  writeFileSync(
    `${home}/cordis.patch.yml`,
    `${systemLayer}- id: acp\n  config:\n    provider: deepseek-official\n    model: ${JSON.stringify(model)}\n${attachLayer}`,
    "utf8",
  );
}

function writeLastMessage() {
  const target = process.env.EVAL_LAST_MESSAGE_PATH || "/workspace/trace/agent-last-message.txt";
  writeFileSync(target, lastMessage.trim() ? `${lastMessage.trim()}\n` : "", "utf8");
}

try {
  await request("initialize", { protocolVersion: 1, clientCapabilities: {} });
  await request("authenticate", { methodId: "evaluation" });
  const session = await request("session/new", { cwd, mcpServers });
  if (!session.sessionId) throw new Error("DSH ACP session/new returned no sessionId");
  const result = await request("session/prompt", {
    sessionId: session.sessionId,
    prompt: [{ type: "text", text: prompt }],
  });
  try { await request("session/close", { sessionId: session.sessionId }); } catch { /* best effort */ }
  writeLastMessage();
  send({ type: "agent.lifecycle", event: "run_completed", stopReason: result.stopReason });
  server.stdin.end();
  const exitCode = await new Promise((resolve) => server.once("close", resolve));
  if (result.stopReason === "error" || result.stopReason === "cancelled") process.exitCode = 1;
  else if (exitCode !== 0) process.exitCode = exitCode;
} catch (error) {
  writeLastMessage();
  process.stderr.write(`${error.stack || error}\n`);
  server.kill("SIGKILL");
  process.exitCode = 1;
}
