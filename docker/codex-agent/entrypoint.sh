#!/bin/sh
set -eu

: "${EVAL_GATEWAY_API_KEY:?EVAL_GATEWAY_API_KEY is required}"

mkdir -p "$CODEX_HOME"
cat > "$CODEX_HOME/config.toml" <<EOF
model_provider = "eval"
model = "${EVAL_MODEL}"
model_reasoning_effort = "${EVAL_REASONING_EFFORT}"
disable_response_storage = true

[model_providers.eval]
name = "AI-Native Evaluation Gateway"
base_url = "${EVAL_GATEWAY_URL}"
wire_api = "${EVAL_WIRE_API}"
requires_openai_auth = true
env_key = "EVAL_GATEWAY_API_KEY"

EOF

if [ "${EVAL_OUTER_SANDBOX:-docker}" = "docker" ]; then
  exec codex --dangerously-bypass-approvals-and-sandbox "$@"
else
  exec codex "$@"
fi
