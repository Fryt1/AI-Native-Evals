# AI-Native-Evals

基于 [Inspect AI](https://inspect.aisi.org.uk/) 的 Agent 评测套件，用于在同一组任务和真实世界验收规则下比较 Codex、AI-Native-DSH 以及未来的其他 Agent。

本仓库**不是** Inspect AI 的 fork，也不重新实现评测 Runner。Inspect AI 是外部依赖；本仓库只维护：

- 评测任务集（Task / Dataset）
- Agent 适配器（Solver）
- Blender/UE5 环境适配（Sandbox / Worker）
- AI-Native-Game-Engine 领域评分器（Scorer）
- 实验条件、失败分类和报告入口

## 架构

```text
Inspect AI
    ↓
AI-Native-Evals
    ├── Task / Dataset
    ├── Codex Solver
    ├── DSH Solver
    ├── Windows DCC Sandbox
    └── Game Engine Scorer
            ↓
        StageResult / Evidence / Artifact
```

## 目录

```text
src/ai_native_evals/
├── tasks/       Inspect Task 定义
├── solvers/     被测 Agent 接入
├── scorers/     领域结果验收
├── sandboxes/   工作区和 DCC 环境
└── adapters/    外部进程、协议和日志适配

datasets/        development / guardian / holdout / challenge
fixtures/        可复现的初始工作区
experiments/     预定义实验条件
```

## 快速开始

使用 uv：

```powershell
uv sync --dev
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
```

查看帮助：

```powershell
uv run inspect eval --help
```

当前 `smoke` task 不调用真实模型，只验证 Inspect Task、Solver、Scorer 三个接口能够组合运行。`codex_file_smoke` 已接入本机 Codex CLI：它在独立 `run_dir` 创建 `hello.txt`，然后由文件状态 Scorer 验收。DSH 和真实 Blender/UE5 环境会在后续提交中接入。

## 评测原则

1. Agent 是可替换的黑盒 Solver。
2. 任务成功由真实世界状态、Evidence 和 Game Engine Scorer 判定，不由 Agent 自述判定。
3. Codex 与 DSH 使用相同任务、Fixture、工具面、限制和评分器时，结果才具有可比性。
4. 每次运行记录 Inspect 版本、Agent 版本、模型、Prompt/Profile、Game Engine commit、Fixture hash 和环境版本。
5. 评测集分为 development、guardian、holdout、challenge，避免针对固定样本过拟合。

## 当前状态

这是第一版骨架。它刻意不包含：

- Inspect AI 源码副本
- 自动调用 UE5/Blender 的隐藏调度器
- 复制 Game Engine 的 Stage 验收逻辑
- 只依赖自然语言或 LLM-as-Judge 的主评分


## First real Agent smoke

Run the real Codex smoke task from the repository root:

```powershell
uv run inspect eval src/ai_native_evals/tasks/codex_file_smoke.py@codex_file_smoke --model mockllm/model
```

The run writes `codex-events.jsonl`, `codex-stderr.log`, `codex-last-message.txt`, and `run-manifest.json` under `runs/codex-file-smoke/<run-id>/`. The scorer checks the actual `hello.txt` bytes; it does not trust the final Agent message. Codex lifecycle, command execution, tool results, Agent messages, and file-change events are also projected into the Inspect Messages/Transcript view.

## Build the sandbox images

Build the gateway and Codex Agent images with one command. `-UseMirror` uses
Docker's reachable registry mirror when direct Docker Hub authentication is
unavailable on the current WSL network:

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -UseMirror
```

The build pins the Codex version and the official Blender MCP source commit.
The DSH image can use the same runtime contract and is selected by changing
`agents.dsh.image` in `config/eval.yaml`.

Build the full-MCP image (Blender + ComfyUI stdio servers inside the image):

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -UseMirror -IncludeAllMcp
```

The local Codex package cache (`cache/codex`) is downloaded once by
`tools/prepare-codex-cache.mjs`; it is never committed.

## Manual run lifecycle

The CLI keeps configuration small, snapshots repositories before an Agent
starts, and owns the Docker sandbox lifecycle:

```powershell
# One-shot: snapshot → Docker sandbox → Agent → wait → cleanup resources
uv run ai-native-evals run execute blender-cube --agent codex

# Or control each lifecycle phase manually
uv run ai-native-evals run prepare blender-cube --agent codex
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run logs <run-id>
uv run ai-native-evals run wait <run-id>
uv run ai-native-evals run status <run-id>
uv run ai-native-evals run cleanup <run-id>
```

`run start` uses the WSL-backed Docker CLI, creates a per-run network, starts
an isolated LLM Gateway and Agent container, and mounts the prepared snapshot
read-write at `/workspace/game-engine`. The Agent container has an isolated
`CODEX_HOME`, no host `~/.codex` mount, a read-only image root, dropped Linux
capabilities, no-new-privileges, memory/PID limits, and per-run writable
workspace/evidence/trace mounts. `run wait` persists the container log and
releases the network and containers.

MCP is a per-run capability, not a global Codex setting. The selected profile
is snapshotted to `agent-config/mcp-servers.json` and projected into the
isolated Agent configuration. The current project profiles cover:

```text
blender-host  → official Blender MCP stdio server → host Blender
ue5-host      → native UE5 streamable HTTP MCP endpoint
comfyui-host  → official comfy-mcp stdio server → host ComfyUI API
research-host → Hugging Face streamable HTTP MCP endpoint
all-host      → all of the above
```

The full Agent image includes the official Blender MCP Server and the
official ComfyUI MCP client when the `all-mcp` image is selected. That MCP Server runs in Docker; the Blender Add-on bridge remains in
the host Blender process and is reached through `BLENDER_MCP_HOST` and
`BLENDER_MCP_PORT`. UE5 and ComfyUI remain host services as well. MCP server
commands are standard provider binaries; this repository does not add an
Agent-side socket client or protocol adapter.

`AI-Native-DSH` receives the same run-scoped MCP descriptors projected to its
standard ACP `mcpServers` shape, so Codex and DSH use the same capability
contract without sharing global configuration.

Defaults live in `config/eval.yaml`; credentials remain in the ignored
`config/.env.local`. The prepared run records immutable Game Engine and
AI-Native-DSH snapshots in `EvalRuns/<run-id>/`.
