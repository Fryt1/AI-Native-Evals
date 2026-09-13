// A single-command Agent with a real tool loop.
//
// The point of this template is that adding an Agent of this shape is a profile
// plus a Dockerfile and no framework code. To be a useful template it has to
// actually do work: a version that only asked the model for text passed the
// verification check while failing every Task that needed an artifact, because
// answering a question and completing a task are different abilities.
//
// It runs the model in a loop, executing the tools the model asks for, until the
// model stops asking. That is the same shape every command-line coding Agent
// uses; a real Agent replaces this file with its own CLI.

import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import http from "node:http";
import https from "node:https";

const gateway = (process.env.EVAL_GATEWAY_URL || "").replace(/\/$/, "");
const apiKey = process.env.EVAL_GATEWAY_API_KEY || "";
const model = process.env.EVAL_MODEL || "";
const promptFile = process.env.EVAL_TASK_PROMPT_FILE || "/run-config/task-prompt.md";
const workspace = process.env.EVAL_WORKDIR || "/workspace";
const traceDir = process.env.EVAL_TRACE_DIR || "/tmp/trace";
const lastMessage = process.env.EVAL_LAST_MESSAGE_PATH || `${traceDir}/agent-last-message.txt`;

//: How many model turns before giving up. A loop that cannot terminate is worse
//: than a failed run, because it holds the container until the run timeout.
const MAX_TURNS = 24;

function emit(event) {
  process.stdout.write(JSON.stringify(event) + "\n");
}

function fail(message, code = 1) {
  emit({ type: "error", message });
  process.exit(code);
}

for (const [name, value] of Object.entries({
  EVAL_GATEWAY_URL: gateway,
  EVAL_GATEWAY_API_KEY: apiKey,
  EVAL_MODEL: model,
})) {
  if (!value) fail(`${name} is required`, 2);
}

let prompt;
try {
  prompt = readFileSync(promptFile, "utf8");
} catch (err) {
  fail(`No prompt at ${promptFile}: ${err.message}`, 2);
}

mkdirSync(traceDir, { recursive: true });
mkdirSync(dirname(lastMessage), { recursive: true });

// The tools this Agent offers. A coding Agent needs at least these two; anything
// narrower cannot complete a task that produces an artifact.
const TOOLS = [
  {
    type: "function",
    name: "run_command",
    description: "Run a shell command inside the workspace and return its output.",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", description: "The shell command to run." },
      },
      required: ["command"],
      additionalProperties: false,
    },
  },
  {
    type: "function",
    name: "write_file",
    description: "Write a file. Creates parent directories as needed.",
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "Absolute path to write." },
        content: { type: "string", description: "Full file content." },
      },
      required: ["path", "content"],
      additionalProperties: false,
    },
  },
  {
    type: "function",
    name: "read_file",
    description: "Read a file and return its content.",
    parameters: {
      type: "object",
      properties: { path: { type: "string", description: "Absolute path to read." } },
      required: ["path"],
      additionalProperties: false,
    },
  },
];

function runCommand(command) {
  return new Promise((done) => {
    execFile("/bin/sh", ["-lc", command], { cwd: workspace, timeout: 120000, maxBuffer: 4 << 20 }, (err, stdout, stderr) => {
      const code = err && typeof err.code === "number" ? err.code : err ? 1 : 0;
      done({ exit_code: code, stdout: String(stdout || ""), stderr: String(stderr || "") });
    });
  });
}

async function callTool(name, args) {
  try {
    if (name === "run_command") return await runCommand(String(args.command || ""));
    if (name === "write_file") {
      const target = resolve(String(args.path || ""));
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, String(args.content ?? ""), "utf8");
      return { ok: true, bytes: Buffer.byteLength(String(args.content ?? "")) };
    }
    if (name === "read_file") {
      return { content: readFileSync(resolve(String(args.path || "")), "utf8") };
    }
    return { error: `unknown tool ${name}` };
  } catch (err) {
    // A tool error is information for the model, not a reason to stop: the model
    // may be able to correct itself.
    return { error: String(err && err.message ? err.message : err) };
  }
}

function post(body) {
  const url = new URL(`${gateway}/responses`);
  const client = url.protocol === "https:" ? https : http;
  const payload = JSON.stringify(body);
  return new Promise((done, reject) => {
    const request = client.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
      },
      (response) => {
        let raw = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { raw += chunk; });
        response.on("end", () => {
          if (response.statusCode !== 200) {
            reject(new Error(`gateway returned ${response.statusCode}: ${raw.slice(0, 300)}`));
            return;
          }
          try {
            done(JSON.parse(raw));
          } catch (err) {
            reject(new Error(`could not parse the gateway response: ${err.message}`));
          }
        });
      },
    );
    request.on("error", reject);
    request.setTimeout(300000, () => request.destroy(new Error("timed out")));
    request.write(payload);
    request.end();
  });
}

function textOf(response) {
  let text = "";
  for (const item of response.output || []) {
    if (item.type !== "message") continue;
    for (const part of item.content || []) {
      if (part.type === "output_text" || part.type === "text") text += part.text || "";
    }
  }
  return text;
}

async function main() {
  emit({ type: "thread.started", thread_id: "example-cli" });
  emit({ type: "turn.started" });

  // The conversation so far, replayed on each turn. The Responses API is
  // stateless from our side, so the whole history goes back each time.
  let input = [{ role: "user", content: prompt }];
  let finalText = "";
  let turns = 0;

  while (turns < MAX_TURNS) {
    turns += 1;
    const response = await post({ model, input, tools: TOOLS });

    const calls = (response.output || []).filter((item) => item.type === "function_call");
    const text = textOf(response);
    if (text) finalText = text;

    // Everything the model produced belongs in the next request's history,
    // including the calls themselves.
    input = [...input, ...(response.output || [])];

    if (!calls.length) break;

    for (const call of calls) {
      let args = {};
      try {
        args = JSON.parse(call.arguments || "{}");
      } catch {
        args = {};
      }
      const result = await callTool(call.name, args);
      emit({
        type: "item.completed",
        item: { id: call.call_id || call.id, type: "tool_call", tool: call.name, arguments: args, result },
      });
      input.push({
        type: "function_call_output",
        call_id: call.call_id || call.id,
        output: JSON.stringify(result),
      });
    }
  }

  if (turns >= MAX_TURNS) {
    finalText += `\n\n[stopped after ${MAX_TURNS} model turns without finishing]`;
  }

  writeFileSync(lastMessage, finalText + "\n", "utf8");
  emit({ type: "item.completed", item: { id: "final", type: "agent_message", text: finalText } });
  emit({ type: "turn.completed" });
  process.stdout.write(finalText + "\n");
}

main().catch((err) => fail(String(err && err.message ? err.message : err)));
