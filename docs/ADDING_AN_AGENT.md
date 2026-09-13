# Adding an Agent that runs as a single command

Most command-line Agents — `claude`, `gemini`, `aider`, `cursor-agent`, and the
next one — share one shape: they take a prompt, run, and print a result. For
those, **a profile is the whole integration**. No Python, no adapter class.

This document is the recipe, and `profiles/agents/example-cli-agent.yaml.txt`
next to it is a template you can copy.

## What a run actually does

Worth knowing, because it explains why a profile is enough:

```
host                          container
  │                                │
  ├─ prepare the run ──────────────┤
  │   · write the Task prompt      │
  │     to /run-config/task-prompt.md
  │   · write MCP servers to       │
  │     /run-config/mcp-servers.json
  │   · mount the workspace        │
  │                                │
  ├─ docker run <image> ───────────┤
  │   with the profile's           │
  │   entrypoint + command         │
  │                                │
  └─ read /workspace/trace ◄───────┘
      and evidence
```

The container is started with **the profile's `entrypoint` and `command`**. The
framework does not know or care which Agent is inside; it supplies the prompt
and reads back what the Agent wrote.

`EVAL_AGENT_ADAPTER` is passed into the container, but nothing in the image
reads it — the container's behaviour comes entirely from `entrypoint`,
`command` and `environment`. That is why a new single-command Agent needs no
framework code.

## The recipe

Create `profiles/agents/<your-agent>.yaml`:

```yaml
id: my-agent
label: My Agent（一行说明）
description: 一两句话说明它是什么、什么时候用。
adapter: codex                       # see "adapter" below
image_repository: ai-native-my-agent
agent_version: 1.0.0                 # tag = <repository>:<version>
protocol: responses                  # how the model is called
workdir: /workspace
writable_paths:
  - /tmp/home                        # anywhere the Agent writes outside /workspace
system_prompt: |                     # optional: prepended by the entrypoint
  You are being evaluated. Work only inside /workspace.
environment:
  HOME: /tmp/home
  MY_AGENT_MODEL: ${MODEL}
  MY_AGENT_REASONING: ${REASONING_EFFORT}
capabilities:
  - filesystem
  - shell
options:
  executable: my-agent
```

Then create the image. The Dockerfile is the only file besides the profile:

```dockerfile
ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}
ARG MY_AGENT_VERSION=1.0.0
LABEL ai.native.agent="my-agent" \
      ai.native.agent.version="$MY_AGENT_VERSION"
RUN npm install --global --no-fund --no-audit "my-agent@${MY_AGENT_VERSION}"
RUN mkdir -p /workspace /tmp/home && chown -R node:node /workspace /tmp/home
USER node
WORKDIR /workspace
ENTRYPOINT ["my-agent", "--prompt-file"]
```

Build it and tag it with the version:

```powershell
wsl.exe -e docker build -f docker/my-agent/Dockerfile -t ai-native-my-agent:1.0.0 .
```

That is the whole thing. `doctor` will list the new Agent, the Console will show
it, and it can be verified with the static and smoke checks.

## The three fields that matter

### `command` — how the prompt reaches the Agent

The Task prompt is a **file**, `$EVAL_TASK_PROMPT_FILE` (default
`/run-config/task-prompt.md`). It is never passed as an argv element: prompts
are Markdown, and an earlier inline version silently lost a table's rows.

Most Agents accept a prompt one of these ways:

```yaml
# 1. The Agent can read a file itself
command: ["--prompt-file", "${TASK_PROMPT}"]

# 2. The Agent reads stdin
entrypoint: sh
command: ["-c", "my-agent --print < ${TASK_PROMPT}"]

# 3. The Agent takes the prompt as an argument (small prompts only)
entrypoint: sh
command: ["-c", "my-agent \"$(cat ${TASK_PROMPT})\""]
```

Prefer 1 or 2. Option 3 goes through a shell, so a prompt containing quotes,
backticks or `$(...)` is partially interpreted — the same class of bug as the
inline-argv one.

### `environment` — how the model and MCP reach the Agent

These placeholders are substituted from the resolved run:

| Placeholder | Becomes |
| --- | --- |
| `${MODEL}` | the chosen model id |
| `${MODEL_PROVIDER}` | the provider profile id |
| `${REASONING_EFFORT}` | the chosen reasoning level |
| `${WORKDIR}` | the profile's `workdir` |
| `${MCP_CONFIG}` | path to the rendered MCP server config |
| `${TASK_PROMPT}` | path to the prompt file |
| `${TASK_ID}`, `${RUN_ID}` | the run's identifiers |

`${MCP_CONFIG}` is a JSON file in the shape Codex uses. An Agent with a
different MCP config format needs a small renderer — that is a real integration
cost, not a profile.

### `writable_paths` — anywhere the Agent writes

The container runs with a read-only root. Every path an Agent writes to must be
listed, or the write fails inside the container with a permission error that
looks like an Agent bug. `/workspace` is writable already; add the Agent's home
and cache directories.

## What still needs code

| Situation | Why | Cost |
| --- | --- | --- |
| The Agent needs a **long-lived protocol** (JSON-RPC, ACP, websocket) | A one-shot command cannot express a handshake | A bridge script, like `docker/dsh-agent/acp-runner.mjs` |
| The Agent's **MCP config format** differs | The framework renders Codex's shape | A small renderer |
| The Agent's **output needs parsing** for trace/process scoring | Normalized events come from the Agent's log | A parser, like `adapters/codex_events.py` |

Everything else is YAML.

## Why `adapter` still exists

`adapter` selects behaviour on the **Inspect solver** path, which runs an Agent
in-process (`src/ai_native_evals/solvers/agent.py`). The Docker path — the one
the Console and `run execute` use — does not consult it.

For a single-command Agent in Docker, set `adapter` to an existing id whose
event parsing is closest, so the trace is still normalized. `codex` is the safe
default: its parser reads a line-delimited JSON stream and passes anything it
does not recognise through untouched.

## Checklist

1. `profiles/agents/<id>.yaml` — the profile
2. `docker/<id>/Dockerfile` — installs the CLI, tags by version
3. Build the image, tagged `<repository>:<version>`
4. `pwsh -File tools/eval.ps1 check -Task <task>` — resolves and preflights
5. Environment page → **静态检查** then **真实测试** — proves the container
   starts and the Agent answers
