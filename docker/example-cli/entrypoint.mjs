// A minimal single-command Agent, used to prove that adding one is a profile
// plus a Dockerfile and nothing else.
//
// It reads the Task prompt from the file the framework mounted, calls the same
// gateway every other Agent calls, and writes the answer where the framework
// looks for it. A real Agent replaces the model call with its own CLI.
//
// Node rather than curl: the base image has no curl, and a real Agent here
// would be a Node CLI anyway.

import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import http from "node:http";
import https from "node:https";

const gateway = process.env.EVAL_GATEWAY_URL;
const apiKey = process.env.EVAL_GATEWAY_API_KEY;
const model = process.env.EVAL_MODEL;
const promptFile = process.env.EVAL_TASK_PROMPT_FILE || "/run-config/task-prompt.md";
const lastMessage = process.env.EVAL_LAST_MESSAGE_PATH || "/tmp/trace/agent-last-message.txt";
const traceDir = process.env.EVAL_TRACE_DIR || "/tmp/trace";

function fail(message, code = 1) {
  process.stdout.write(JSON.stringify({ type: "error", message }) + "\n");
  process.exit(code);
}

for (const [name, value] of Object.entries({ EVAL_GATEWAY_URL: gateway, EVAL_GATEWAY_API_KEY: apiKey, EVAL_MODEL: model })) {
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

// The prompt is read from the file, never from argv: it is Markdown, and
// passing it inline loses its structure.
const body = JSON.stringify({ model, input: prompt });
const url = new URL(`${gateway.replace(/\/$/, "")}/responses`);
const client = url.protocol === "https:" ? https : http;

process.stdout.write(JSON.stringify({ type: "thread.started", thread_id: "example-cli" }) + "\n");
process.stdout.write(JSON.stringify({ type: "turn.started" }) + "\n");

const request = client.request(
  {
    hostname: url.hostname,
    port: url.port || (url.protocol === "https:" ? 443 : 80),
    path: url.pathname + url.search,
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
    },
  },
  (response) => {
    let raw = "";
    response.setEncoding("utf8");
    response.on("data", (chunk) => { raw += chunk; });
    response.on("end", () => {
      if (response.statusCode !== 200) {
        fail(`gateway returned ${response.statusCode}: ${raw.slice(0, 300)}`);
      }
      let text = "";
      try {
        const parsed = JSON.parse(raw);
        for (const item of parsed.output || []) {
          if (item.type !== "message") continue;
          for (const part of item.content || []) {
            if (part.type === "output_text" || part.type === "text") text += part.text || "";
          }
        }
      } catch (err) {
        fail(`could not parse the gateway response: ${err.message}`);
      }
      writeFileSync(lastMessage, text + "\n", "utf8");
      process.stdout.write(
        JSON.stringify({ type: "item.completed", item: { id: "item_0", type: "agent_message", text } }) + "\n",
      );
      process.stdout.write(JSON.stringify({ type: "turn.completed" }) + "\n");
      process.stdout.write(text + "\n");
    });
  },
);

request.on("error", (err) => fail(`request failed: ${err.message}`));
request.setTimeout(120000, () => { request.destroy(new Error("timed out")); });
request.write(body);
request.end();
