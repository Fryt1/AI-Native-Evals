#!/bin/sh
# Launch pi for one evaluation run.
#
# The task prompt arrives as a mounted file, never as an argument: prompts are
# Markdown with newlines, pipes and backticks, and handing that to `docker run`
# as one argv entry loses its structure. pi takes the prompt as its final
# positional argument, so this wrapper reads the file and passes the text.
set -eu

prompt_file="${EVAL_TASK_PROMPT_FILE:-/run-config/task-prompt.md}"
if [ ! -f "$prompt_file" ]; then
  echo "pi entrypoint: no task prompt at $prompt_file" >&2
  exit 64
fi

agent_dir="${PI_CODING_AGENT_DIR:-/opt/pi-home/agent}"
mkdir -p "$agent_dir"

# pi resolves where a request goes from its model registry, not from
# OPENAI_BASE_URL: that variable is Azure-specific, and setting it changed
# nothing -- every call failed in about 20ms with "Request timed out.", which is
# a local resolution failure rather than a network one.
#
# The registry is written by node, not by a shell heredoc: the values are
# interpolated into JSON, and a shell cannot escape them.
node - "$agent_dir" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

const dir = process.argv[2];
const gateway = process.env.EVAL_GATEWAY_URL || "http://llm-gateway:8080/v1";
const model = process.env.EVAL_MODEL || "deepseek-v4-flash";
const key = process.env.EVAL_GATEWAY_API_KEY || "";

// One provider, named for what it is: the run's own gateway, not OpenAI.
fs.writeFileSync(
  path.join(dir, "models.json"),
  JSON.stringify(
    {
      providers: {
        "eval-gateway": {
          name: "Evaluation gateway",
          baseUrl: gateway,
          api: "openai-completions",
          apiKey: "gateway",
          models: [{ id: model }],
        },
      },
    },
    null,
    2,
  ),
);

// pi reads credentials per provider from auth.json. Writing it here keeps the
// key off the command line, where the process list would show it.
fs.writeFileSync(
  path.join(dir, "auth.json"),
  JSON.stringify({ "eval-gateway": { type: "api_key", key } }, null, 2),
);
NODE

set -- --print --mode json --provider eval-gateway --model "${EVAL_MODEL:-deepseek-v4-flash}"

if [ -n "${EVAL_REASONING_EFFORT:-}" ]; then
  set -- "$@" --thinking "$EVAL_REASONING_EFFORT"
fi
if [ -f "${EVAL_SYSTEM_PROMPT_FILE:-}" ]; then
  set -- "$@" --system-prompt "$(cat "$EVAL_SYSTEM_PROMPT_FILE")"
fi

set -- "$@" "$(cat "$prompt_file")"
exec pi "$@"
