# Adding an Agent

Read this first, because the honest answer is not one number.

**How much work it is depends entirely on what the Agent already does.**
A finished command-line coding Agent is a profile. A thin wrapper around a chat
model is a program. Those are not the same task and treating them as one wastes
a day.

| The Agent is… | You write | Because |
| --- | --- | --- |
| **A finished CLI** — `claude`, `gemini`, `aider`, `codex`, `dsh` | A profile and a Dockerfile | It already has a tool loop, a file editor and a shell. You are only telling the framework how to start it. |
| **A model call** — your own script that posts a prompt and prints the reply | A profile, a Dockerfile, **and the tool loop** | It can answer a question but cannot touch a file. Every Task that must produce an artifact will fail. |
| **A new protocol** — JSON-RPC, ACP, a socket handshake | All of the above **plus a bridge** | A one-shot command cannot express a handshake. See `docker/dsh-agent/acp-runner.mjs`. |

`docker/example-cli/` is the second kind, written out in full. It is the useful
one to read, because the first kind needs no explanation and the third is rare.

## Why a profile is enough for a finished CLI

The container is started with the profile's `entrypoint` and `command`. The
framework does not know which Agent is inside: it mounts the Task prompt, mounts
the MCP configuration, and reads back the trace the Agent wrote.

`EVAL_AGENT_ADAPTER` is passed into the container, but nothing in the image reads
it. Behaviour comes entirely from `entrypoint`, `command` and `environment`.

## A finished CLI: profile and Dockerfile

`profiles/agents/<id>.yaml`:

```yaml
id: my-agent
label: My Agent
image_repository: ai-native-my-agent
agent_version: 1.0.0
workdir: /workspace
writable_paths:
  - /tmp/home
options:
  trace_parser: codex
```

`docker/my-agent/Dockerfile`:

```dockerfile
ARG CODEX_BASE_IMAGE=ai-native-codex-agent:0.153.4
FROM ${CODEX_BASE_IMAGE}
ARG MY_AGENT_VERSION=1.0.0
LABEL ai.native.agent="my-agent" ai.native.agent.version="$MY_AGENT_VERSION"
USER root
RUN npm install --global --no-fund --no-audit "my-agent@${MY_AGENT_VERSION}"
USER node
WORKDIR /workspace
ENTRYPOINT ["my-agent", "--print"]
```

Then:

```powershell
wsl.exe -e docker build -f docker/my-agent/Dockerfile -t ai-native-my-agent:1.0.0 .
pwsh -File tools/eval.ps1 check -Task <task-id>
```

The Environment page verifies it: **静态检查**, then **真实测试**.

### The three fields that matter

**`command`** — how the prompt reaches the Agent. The prompt is a **file** at
`$EVAL_TASK_PROMPT_FILE`. Read it; do not expect it in argv.

```yaml
# The CLI reads a file itself
command: ["--prompt-file", "${TASK_PROMPT}"]
# The CLI reads stdin
entrypoint: sh
command: ["-c", "my-agent --print < ${TASK_PROMPT}"]
```

Avoid putting the prompt text in argv through a shell. A prompt is Markdown: it
contains quotes, backticks and `$(...)`, and a shell will act on them. This is
not hypothetical — passing the prompt inline once lost a table's rows, and the
Agent reported the missing names as an ambiguity in the request.

**`environment`** — the framework already exports `EVAL_MODEL`,
`EVAL_GATEWAY_URL`, `EVAL_GATEWAY_API_KEY`, `EVAL_REASONING_EFFORT`,
`EVAL_TASK_PROMPT_FILE` and `EVAL_MCP_SERVERS_FILE`. Declare only the names your
Agent uses, mapped to those:

```yaml
environment:
  MY_AGENT_MODEL: ${EVAL_MODEL}
```

**`writable_paths`** — the container root is read-only. Anything the Agent
writes outside `/workspace` must be listed, or the write fails with a permission
error inside the container that looks like an Agent bug. An Agent that keeps a
cache or config in `$HOME` needs `$HOME` listed.

## A model call: you must write the tool loop

This is what `docker/example-cli/entrypoint.mjs` does. The loop is:

```
send the prompt + tool definitions to the model
  → the model asks for a tool
  → run the tool
  → send the result back
  → repeat until the model asks for nothing
```

The tools a coding task needs are small — `run_command`, `write_file`,
`read_file` — but without them the Agent can only talk.

Two things that are easy to get wrong:

- **Bound the loop.** An unbounded loop holds the container until the run
  timeout. The template stops after 24 turns and says so in its final message.
- **Return tool errors to the model** rather than exiting. The model can often
  correct itself; the run cannot.

Confirm the upstream supports tool calling before building on it. The gateway
proxies `/v1/responses`, and whether `function_call` comes back is a property of
the model behind it, not of the framework.

## A new protocol: also a bridge

DSH speaks ACP, a long-lived JSON-RPC session, so a one-shot command cannot
express it. `docker/dsh-agent/acp-runner.mjs` performs the handshake and drives
one turn.

## What the framework provides

| Placeholder | Becomes |
| --- | --- |
| `${TASK_PROMPT}` | path to the prompt file — never the text |
| `${MODEL}`, `${MODEL_PROVIDER}` | the resolved model and provider |
| `${REASONING_EFFORT}` | the resolved reasoning level |
| `${WORKDIR}` | the profile's `workdir` |
| `${MCP_CONFIG}` | path to the rendered MCP server config |
| `${TASK_ID}`, `${RUN_ID}` | the run's identifiers |

`${MCP_CONFIG}` is Codex's JSON shape. An Agent with a different MCP config
format needs a small renderer — a real cost, not a profile field.

## Checklist

1. Decide which of the three kinds the Agent is. This determines everything else.
2. `profiles/agents/<id>.yaml`
3. `docker/<id>/Dockerfile`, tagged `<repository>:<version>`
4. Build it
5. `pwsh -File tools/eval.ps1 check -Task <task-id>`
6. Environment page → **静态检查** → **真实测试**
7. Run a real Task. Answering a question and completing a task are different
   abilities, and only the second one is graded.
