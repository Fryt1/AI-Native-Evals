import fs from "node:fs";
import path from "node:path";

const [outputPath, serversPath = ""] = process.argv.slice(2);
if (!outputPath) throw new Error("output config path is required");

let servers = {};
if (serversPath && fs.existsSync(serversPath)) {
  servers = JSON.parse(fs.readFileSync(serversPath, "utf8"));
}
if (process.env.EVAL_ENABLE_BLENDER_MCP === "1" && !servers.blender) {
  servers.blender = {
    transport: "stdio",
    command: "/opt/blender-mcp/bin/blender-mcp",
    env: {
      BLENDER_MCP_HOST: process.env.BLENDER_MCP_HOST || "host.docker.internal",
      BLENDER_MCP_PORT: process.env.BLENDER_MCP_PORT || "9876",
    },
  };
}

const lines = [
  `model_provider = ${tomlString(process.env.EVAL_MODEL_PROVIDER || "eval")}`,
  `model = ${tomlString(process.env.EVAL_MODEL || "")}`,
  `model_reasoning_effort = ${tomlString(process.env.EVAL_REASONING_EFFORT || "high")}`,
  "disable_response_storage = true",
  "",
  "[model_providers.eval]",
  'name = "AI-Native Evaluation Gateway"',
  `base_url = ${tomlString(process.env.EVAL_GATEWAY_URL || "http://llm-gateway:8080/v1")}`,
  `wire_api = ${tomlString(process.env.EVAL_WIRE_API || "responses")}`,
  "requires_openai_auth = true",
  'env_key = "EVAL_GATEWAY_API_KEY"',
];

for (const [name, descriptor] of Object.entries(servers || {})) {
  appendServer(lines, name, descriptor);
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${lines.join("\n")}\n`, "utf8");

function appendServer(lines, name, descriptor) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
    throw new Error(`invalid MCP server name: ${name}`);
  }
  if (!descriptor || typeof descriptor !== "object") {
    throw new Error(`MCP server ${name} must be an object`);
  }
  const transport = descriptor.transport || (descriptor.url ? "streamable-http" : "stdio");
  lines.push("", `[mcp_servers.${tomlKey(name)}]`);
  if (transport === "stdio") {
    if (!descriptor.command) throw new Error(`MCP stdio server ${name} needs command`);
    lines.push(`command = ${tomlString(descriptor.command)}`);
    if (Array.isArray(descriptor.args) && descriptor.args.length) {
      lines.push(`args = ${tomlArray(descriptor.args)}`);
    }
    if (descriptor.cwd) lines.push(`cwd = ${tomlString(descriptor.cwd)}`);
    appendScalar(lines, descriptor, "startup_timeout_sec");
    appendScalar(lines, descriptor, "tool_timeout_sec");
    appendEnv(lines, name, descriptor.env);
    return;
  }
  if (transport === "streamable-http" || transport === "http") {
    if (!descriptor.url) throw new Error(`MCP HTTP server ${name} needs url`);
    lines.push(`url = ${tomlString(descriptor.url)}`);
    if (descriptor.bearer_token_env_var) {
      lines.push(`bearer_token_env_var = ${tomlString(descriptor.bearer_token_env_var)}`);
    }
    appendScalar(lines, descriptor, "startup_timeout_sec");
    appendScalar(lines, descriptor, "tool_timeout_sec");
    return;
  }
  throw new Error(`unsupported MCP transport for ${name}: ${transport}`);
}

function appendEnv(lines, name, env) {
  if (!env || typeof env !== "object" || !Object.keys(env).length) return;
  lines.push("", `[mcp_servers.${tomlKey(name)}.env]`);
  for (const [key, value] of Object.entries(env)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw new Error(`invalid MCP env key: ${key}`);
    lines.push(`${key} = ${tomlString(String(value))}`);
  }
}

function appendScalar(lines, descriptor, key) {
  if (descriptor[key] === undefined || descriptor[key] === null) return;
  const value = descriptor[key];
  if (!Number.isInteger(value) && typeof value !== "boolean") {
    throw new Error(`${key} must be an integer or boolean`);
  }
  lines.push(`${key} = ${String(value)}`);
}

function tomlKey(value) {
  return /^[A-Za-z0-9_-]+$/.test(value) ? value : tomlString(value);
}

function tomlString(value) {
  return JSON.stringify(String(value));
}

function tomlArray(values) {
  return `[${values.map(tomlString).join(", ")}]`;
}
