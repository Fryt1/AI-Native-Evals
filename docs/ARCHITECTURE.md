# AI-Native-Evals 架构

> 本文描述 **2026 年 9 月 8 日** 当前实现。评测框架不 fork Inspect AI；Inspect AI 仍然负责 Task、Solver、Scorer、运行记录与 Viewer。

## 一句话

`AI-Native-Evals` 把 **Task 定义**、**Agent 接入**、**Docker 沙箱**、**证据验证** 和 **Inspect 展示** 拆开：换 Codex、DSH 或未来的 Agent，只替换 Agent Profile/Adapter，Task 与 TestPlan 不变。

```text
┌────────────────────────────────────────────────────────────────────┐
│                        AI-Native-Evals                            │
│                                                                    │
│  Task Bundle                  Global Registry                      │
│  ├─ prompt.md                 ├─ profiles/agents/*.yaml            │
│  ├─ task.yaml                 ├─ profiles/models/*.yaml            │
│  ├─ rubric.yaml               ├─ config/eval.yaml                  │
│  ├─ resources[]               └─ Profile catalogs                  │
│  └─ test_plan/checks[]                                             │
│              │                                                     │
│              ▼                                                     │
│       Run Resolver ──────────────── immutable run-manifest.json    │
│              │                                                     │
│              ▼                                                     │
│   Resource Providers ── per-run Workspace ── Snapshot/Evidence      │
│              │                                                     │
│              ▼                                                     │
│   SandboxRuntime (Docker/WSL only)                                 │
│              │  stable env/mount/permission contract               │
│              ▼                                                     │
│   Agent Profile ── Agent Adapter ── external Agent process          │
│       Codex       ── CodexAdapter ── Codex JSONL                   │
│       DSH         ── DshAcpAdapter ── standard ACP JSON-RPC        │
│       future      ── another adapter                               │
│              │                                                     │
│              ▼                                                     │
│   Raw Trace ── Normalized AgentEvent[] ── Inspect Transcript        │
│              │                                                     │
│              ▼                                                     │
│   Evaluation Runner ── Check[] (DAG)                               │
│       ├─ Outcome: Locator Agent + deterministic verifier             │
│       ├─ Quality: Judge Agent + task rubric                         │
│       └─ Process: normalized trace analyzer                          │
│              │                                                     │
│              ▼                                                     │
│   EvaluationReport / verdict.json / digest / Inspect Viewer         │
└────────────────────────────────────────────────────────────────────┘

外部宿主：Windows Blender / UE5 / ComfyUI
        ▲
        └── Agent 容器只通过标准 MCP 访问，不把 DCC 进程塞进容器
```

## 每个模块到底负责什么

### 1. Task Bundle：定义“测什么”

目录 `tasks/<task-id>/` 是一个可提交、可复用的测试单元：

```text
tasks/structured-report-contract/
├── task.yaml       # TestPlan、资源、运行时能力偏好
├── prompt.md       # 给被测 Agent 的任务要求
└── rubric.yaml     # Quality Judge 的评分标准（可选）
```

Task 不引用 Codex API，也不要求 DSH。`execution.agent` 只是默认值，命令行可以用 `--agent dsh` 覆盖。同一个 Task 直接对比两个 Agent；默认的 Outcome/Quality Evaluator 仍使用 `defaults.evaluator_agent`，不会因为被测 Agent 改成 DSH 就改变评分者：

```powershell
uv run ai-native-evals run execute structured-report-contract --agent codex
uv run ai-native-evals run execute structured-report-contract --agent dsh
```

两次运行使用相同的 Prompt、Fixture、TestPlan、Evaluator、资源快照规则和时间限制；唯一变化是被测 Agent Profile，`evaluator_agent_profile` 保持固定。

### 2. Agent Profile：描述“怎么启动一个 Agent”

`profiles/agents/*.yaml` 只描述外部运行时；`profiles/models/*.yaml` 描述模型，`profiles/mcp/*.yaml` 描述工具，`profiles/sandboxes/*.yaml` 描述隔离，`config/presets/*.yaml` 只绑定选择器：

```yaml
id: dsh
adapter: dsh-acp
image: ai-native-dsh-agent:local
protocol: acp
workdir: /workspace
entrypoint: node
command:
  - /run-config/dsh-acp-runner.mjs
  - ${TASK_PROMPT}
```

Profile 可以改镜像、工作目录、可写临时目录、命令模板、能力声明和适配器选项；它不改变共享 Evaluator。

### 3. Agent Adapter：翻译“具体 Agent 协议”

Adapter 是真正的外部协议 seam：

- `CodexAdapter`：启动 Codex，保存 Codex 原始 JSONL，并转成统一事件。
- `DshAcpAdapter`：通过标准 ACP `initialize → session/new → session/prompt → session/close` 驱动 DSH；MCP 使用标准 ACP `McpServer` 声明，不使用私有 socket 客户端。
- 新 Agent 只需实现 `AgentAdapter.run(AgentLaunchSpec)`，不需要改 Task、TestPlan 或 Scorer。

`AI-Native-DSH` 不是这里的 DSH Agent 实现；真正的 DSH 源仓库是 `D:\work\AI-Native\dsh`，镜像构建从那里读取。

### 4. SandboxRuntime：只负责隔离，不理解 Agent

Docker/WSL 运行时只做以下事情：

```text
创建每次运行独立 network
启动 LLM Gateway
启动 Agent 容器
挂载 /workspace 和 /run-config
注入模型、推理强度、MCP 文件、运行 ID
限制 root filesystem、capabilities、memory、PIDs
等待、收集日志、清理容器
```

它不应该出现 `if codex` 才能工作的逻辑。Agent-specific 行为全部进入 Profile、镜像 entrypoint 或 Adapter。当前旧 manifest 没有 Profile 时保留 Codex 兼容默认值，但新运行都会写入 `run.agent_profile`。

### 5. Resource Provider：只准备 Task 明确声明的资源

Task 没有声明 Game Engine，就不会复制 Game Engine：

```yaml
resources: []
```

需要项目时才写：

```yaml
resources:
  - id: game-engine
    kind: repository
    source: game_engine
    mount: game-engine
```

`source` 是 `config/eval.yaml` 的 `paths.source_roots` 别名。`repository` 支持 Git ref 或 working tree，`fixture`/`directory` 支持普通目录，`host_service` 只记录宿主服务元数据，不复制进容器。

### 6. Normalized AgentEvent：统一过程数据

原始协议永远保留；Viewer、Process Scorer、Digest 只看统一事件：

```json
{"seq": 4, "type": "tool_call", "agent_id": "dsh", "payload": {...}}
{"seq": 5, "type": "tool_result", "agent_id": "dsh", "payload": {...}}
{"seq": 6, "type": "file_changed", "agent_id": "codex", "payload": {...}}
```

稳定事件类型包括：`run_started`、`turn_started`、`agent_message`、`reasoning`、`tool_call`、`tool_result`、`command_started`、`command_completed`、`file_changed`、`approval_requested`、`error` 和完成事件。这样 Process Scorer 不再判断“这是 Codex 还是 DSH”。

### 7. TestPlan / Check：定义“怎么验收”

一个 `Check` 是：

```text
id + phase + evaluator + input + config + depends_on + policy
```

`depends_on` 形成 DAG。Runner 会按依赖顺序运行；一个 Check 失败时，依赖它的 Check 变成 `blocked`，不会伪造通过。

```text
Outcome Locator
      ↓ selected_artifact
Script Contract Check
      ↓ path
Quality Judge
      ↓
Process Analyzer
```

Evaluator 是可复用实现，Task 只填不同的输入与期望值。

## 一次运行的实际数据流

```text
1. resolve_run
   读取 eval.yaml + Task Bundle + Agent Profile + Model Profile

2. prepare_run
   创建 EvalRuns/<run-id>/workspace/
   只快照 Task.resources
   写入 agent-profile.json、MCP 配置、run-manifest.json

3. start_docker_run
   SandboxRuntime 根据 manifest 启动 gateway + Agent
   Agent 只看到容器里的 /workspace；Blender/UE5 仍在 Windows 宿主

4. wait_docker_run
   等待 Agent，保存 trace/agent-container.log
   生成 trace/normalized-events.jsonl

5. evaluate_run
   冻结并复用同一个 Workspace
   按 TestPlan 执行 Outcome/Quality/Process Checks

6. write report
   evidence/checks/<check-id>.json
   evidence/evaluation.json
   evidence/verdict.json
   trace/digest.json + trace/digest.md
```

## 配置的职责边界

```text
config/eval.yaml       唯一总入口：路径、默认选择器、Profile 目录
profiles/agents/       Agent 镜像、adapter、entrypoint、workdir
profiles/models/       模型、provider、协议、推理强度
profiles/mcp/          MCP 工具和宿主服务
profiles/sandboxes/    Docker/WSL 隔离参数
config/presets/        常用 Agent + Model + MCP + Sandbox 组合
 tasks/<id>/task.yaml  Task resources、TestPlan、默认能力偏好
 tasks/<id>/prompt.md  被测 Agent Prompt
 tasks/<id>/rubric.yaml Quality Judge Rubric
 prompts/outcome/      跨 Task 复用的 Locator 指令模板
 prompts/quality/      跨 Task 复用的 Judge 指令模板
 src/.../evaluators    Evaluator 实现
 EvalRuns/             仓库外运行现场，不提交
```

## 为什么不 fork Inspect AI

Inspect 已经提供 Task、Dataset、Solver、Scorer、Sandbox、日志和 Viewer。我们真正变化的是 Agent 协议、资源快照和领域验收规则，因此把 seam 放在自己的 Adapter/Provider/Evaluator 层，收益是：

- Inspect 升级不会要求维护整套 fork。
- Codex/DSH/未来 Agent 可在同一 TestPlan 上横向比较。
- 领域规则仍由 Task 和 Game Engine verifier 拥有。
- 原始 Trace 与统一 Trace 都可复现、可审计。

只有当 Inspect 缺少明确且无法通过 extension point 表达的能力时，才考虑 fork，并记录最小补丁和 upstream 计划。
