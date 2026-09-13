#!/bin/sh
set -eu

: "${EVAL_GATEWAY_API_KEY:?EVAL_GATEWAY_API_KEY is required}"
: "${BLENDER_MCP_HOST:=host.docker.internal}"
: "${BLENDER_MCP_PORT:=9876}"
TRACE_DIR="${EVAL_TRACE_DIR:-/workspace/trace}"
WORKDIR="${EVAL_WORKDIR:-/workspace}"
LAST_MESSAGE_PATH="${EVAL_LAST_MESSAGE_PATH:-$TRACE_DIR/agent-last-message.txt}"

mkdir -p "$HOME" "$CODEX_HOME" "$TRACE_DIR"
node /usr/local/lib/ai-native/render_codex_config.mjs \
  "$CODEX_HOME/config.toml" \
  "${EVAL_MCP_SERVERS_FILE:-/run-config/mcp-servers.json}"

# Evaluation runs are non-interactive. Always use codex exec so Docker does
# not need to provide a TTY or an interactive stdin stream.
CODEX_ARGS=""
if [ "${EVAL_OUTER_SANDBOX:-docker}" = "docker" ]; then
  CODEX_ARGS="--dangerously-bypass-approvals-and-sandbox"
fi

# The Task prompt arrives as a mounted file, not as an argument. A prompt is
# Markdown -- blank lines, pipes, backticks, lists -- and an argument list cannot
# carry that faithfully: passing it inline lost a table's rows, and the Agent
# reported the missing names as an ambiguity in the request rather than as a
# fault in delivery.
if [ "$#" -eq 0 ]; then
  PROMPT_FILE="${EVAL_TASK_PROMPT_FILE:-/run-config/task-prompt.md}"
  if [ ! -s "$PROMPT_FILE" ]; then
    echo "No prompt: pass one as an argument or set EVAL_TASK_PROMPT_FILE" >&2
    exit 2
  fi
  # Codex reads the instructions from stdin when no PROMPT argument is given.
  exec codex ${CODEX_ARGS} exec \
    --json \
    --ephemeral \
    --skip-git-repo-check \
    --cd "$WORKDIR" \
    --output-last-message "$LAST_MESSAGE_PATH" \
    < "$PROMPT_FILE"
fi

exec codex ${CODEX_ARGS} exec \
  --json \
  --ephemeral \
  --skip-git-repo-check \
  --cd "$WORKDIR" \
  --output-last-message "$LAST_MESSAGE_PATH" \
  "$@"
