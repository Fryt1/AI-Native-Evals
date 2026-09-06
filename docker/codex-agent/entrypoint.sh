#!/bin/sh
set -eu

: "${EVAL_GATEWAY_API_KEY:?EVAL_GATEWAY_API_KEY is required}"
: "${BLENDER_MCP_HOST:=host.docker.internal}"
: "${BLENDER_MCP_PORT:=9876}"

mkdir -p "$HOME" "$CODEX_HOME"
node /usr/local/lib/ai-native/render_codex_config.mjs \
  "$CODEX_HOME/config.toml" \
  "${EVAL_MCP_SERVERS_FILE:-/run-config/mcp-servers.json}"

# Evaluation runs are non-interactive. Always use `codex exec` so Docker does
# not need to provide a TTY or an interactive stdin stream.
CODEX_ARGS=""
if [ "${EVAL_OUTER_SANDBOX:-docker}" = "docker" ]; then
  CODEX_ARGS="--dangerously-bypass-approvals-and-sandbox"
fi

exec codex ${CODEX_ARGS} exec \
  --json \
  --ephemeral \
  --skip-git-repo-check \
  --cd /workspace/game-engine \
  --output-last-message /workspace/trace/codex-last-message.txt \
  "$@"
