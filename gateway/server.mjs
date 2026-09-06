// AI-Native LLM Gateway.
//
// Northbound protocols:
//   POST /v1/responses
//   POST /v1/chat/completions
//   GET  /v1/models
//
// Southbound is an OpenAI-style upstream configured by environment variables.
// The gateway owns the upstream credential; agent containers receive only the
// gateway credential (if GATEWAY_API_KEY is configured).

import http from "node:http";

const port = Number(process.env.PORT || 8080);
const upstreamBase = (process.env.UPSTREAM_BASE_URL || process.env.AI_NATIVE_EVALS_LLM_BASE_URL || "").replace(/\/+$/, "");
const upstreamKey = process.env.UPSTREAM_API_KEY || process.env.AI_NATIVE_EVALS_LLM_API_KEY || "";
const gatewayKey = process.env.GATEWAY_API_KEY || "";
const defaultModel = process.env.DEFAULT_MODEL || process.env.AI_NATIVE_EVALS_LLM_MODEL || "";
const upstreamWire = process.env.UPSTREAM_WIRE_API || process.env.AI_NATIVE_EVALS_LLM_WIRE_API || "responses";

if (!upstreamBase || !upstreamKey) {
  console.error("UPSTREAM_BASE_URL and UPSTREAM_API_KEY are required");
  process.exit(2);
}

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function authorized(req) {
  if (!gatewayKey) return true;
  return req.headers.authorization === `Bearer ${gatewayKey}`;
}

function upstreamUrl(pathname) {
  // Accept either https://host or https://host/v1 as the configured base.
  if (upstreamBase.endsWith("/v1")) {
    if (pathname.startsWith("/v1/")) return `${upstreamBase}${pathname.slice(3)}`;
    return `${upstreamBase}${pathname}`;
  }
  return `${upstreamBase}${pathname}`;
}

function upstreamPath(pathname) {
  if (pathname === "/v1/responses") return "/v1/responses";
  if (pathname === "/v1/chat/completions") return "/v1/chat/completions";
  if (pathname === "/v1/models") return "/v1/models";
  return pathname;
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

async function proxy(req, res, pathname) {
  const body = await readBody(req);
  let outgoing = body;
  if (body.length && req.headers["content-type"]?.includes("application/json")) {
    try {
      const payload = JSON.parse(body.toString("utf8"));
      if (!payload.model && defaultModel) payload.model = defaultModel;
      outgoing = Buffer.from(JSON.stringify(payload));
    } catch {
      // Preserve non-JSON bodies for protocol debugging.
    }
  }

  const target = upstreamUrl(upstreamPath(pathname));
  const headers = {
    "authorization": `Bearer ${upstreamKey}`,
    "content-type": req.headers["content-type"] || "application/json",
    "accept": req.headers.accept || "application/json",
    "user-agent": "ai-native-llm-gateway/0.1",
  };
  if (req.headers["accept-encoding"]) headers["accept-encoding"] = req.headers["accept-encoding"];

  try {
    const options = { method: req.method, headers };
    if (req.method !== "GET" && req.method !== "HEAD" && outgoing.length) options.body = outgoing;
    const upstream = await fetch(target, options);
    const responseHeaders = {
      "content-type": upstream.headers.get("content-type") || "application/json",
      "cache-control": "no-cache",
    };
    res.writeHead(upstream.status, responseHeaders);
    if (upstream.body) {
      for await (const chunk of upstream.body) res.write(chunk);
    }
    res.end();
  } catch (error) {
    json(res, 502, {
      error: {
        type: "gateway_upstream_error",
        message: String(error),
      },
    });
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  if (url.pathname === "/health") return json(res, 200, { ok: true, upstream: upstreamBase, wire_api: upstreamWire });
  if (!authorized(req)) return json(res, 401, { error: { type: "invalid_gateway_key", message: "invalid gateway credential" } });
  if (req.method === "GET" && url.pathname === "/v1/models") return proxy(req, res, url.pathname);
  if (req.method === "POST" && ["/v1/responses", "/v1/chat/completions"].includes(url.pathname)) {
    return proxy(req, res, url.pathname);
  }
  return json(res, 404, { error: { type: "not_found", message: "unsupported gateway route" } });
});

server.listen(port, "0.0.0.0", () => {
  console.log(`AI-Native LLM Gateway listening on :${port}`);
  console.log(`upstream=${upstreamBase} wire_api=${upstreamWire} default_model=${defaultModel || "request"}`);
});
