# Run lifecycle（当前实现）

本文描述 **2026 年 9 月 8 日** 的一次评测如何从 Task 变成可审计的 Run。一次 Run 的 Workspace 在整个评测过程中保持不变：被测 Agent 写入，Outcome/Quality/Process Evaluator 随后复用同一现场读取。

## 状态机

```text
resolved
   │
   ▼
prepared ── start ──► running ── wait ──► completed/failed
   │                                      │
   └────────────── evaluate ◄────────────┘
                         │
                         ▼
                 evaluation written
                         │
                         ▼
                    cleaned
```

`run-manifest.json` 保存状态和不可变决议；Docker runtime 信息单独放在 `runtime` 字段，Evaluator 结果放在 `evaluation` 字段。

## 命令

```powershell
# 发现和验证定义
uv run ai-native-evals task list
uv run ai-native-evals task validate codex-file-smoke
uv run ai-native-evals run plan codex-file-smoke

# 快照并创建一个 Run
uv run ai-native-evals run prepare codex-file-smoke --agent codex

# 启动、查看、等待
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run logs <run-id>
uv run ai-native-evals run wait <run-id>

# 在同一 Workspace 上执行 TestPlan
uv run ai-native-evals run evaluate <run-id>
uv run ai-native-evals run digest <run-id>
uv run ai-native-evals run status <run-id>

# 最后才删除现场
uv run ai-native-evals run cleanup <run-id>
```

一键执行等价于：

```powershell
uv run ai-native-evals run execute <task-id> --preset <preset-id>

# 同一 Task 批量比较多个 Agent
uv run ai-native-evals compare <task-id> --agents codex,dsh-release --preset codex-default
```

## Prepare：只复制 Task 声明的资源

```text
resolve_run
  ├── config/eval.yaml
  ├── profiles/agents/<agent>.yaml
  ├── profiles/models/<model>.yaml
  ├── profiles/mcp/<mcp>.yaml
  ├── profiles/sandboxes/<sandbox>.yaml
  ├── config/presets/<preset>.yaml（可选）
  └── tasks/<task-id>/
          ├── prompt.md
          ├── task.yaml
          └── rubric.yaml
          │
          ▼
prepare_run
  ├── TaskLoader.load()
  ├── ResourceProvider.prepare() for resources[]
  ├── write agent-config/*.json
  └── write run-manifest.json
```

`resources: []` 的 Task 不会在 workspace 里出现任何项目目录。这解决了“所有测试都被默认绑到某个被测项目”的问题：被快照什么，完全由 Task 自己声明。

## Workspace 结构

```text
EvalRuns/<run-id>/
├── run-manifest.json
└── workspace/
    ├── <declared-resource-mount>/
    ├── output/
    ├── scratch/
    ├── artifacts/
    ├── evidence/
    ├── trace/
    └── agent-config/
```

`project`、`dsh` 是旧版本兼容别名。新的真实资源清单在：

```json
{
  "snapshots": {
    "resources": {
      "<resource-id>": {"resource_id": "<resource-id>", "kind": "repository", "...": "..."}
    }
  },
  "paths": {
    "resources": {
      "<resource-id>": "<run-dir>/workspace/<mount>"
    }
  }
}
```

## Docker：只做 Sandbox，不做 Agent 业务

每次 subject Agent Run 创建自己的：

```text
network
gateway container
agent container
```

容器拿到：

- `/workspace`：这个 Run 的唯一可写工作区。
- `/run-config`：本次 Run 的 Agent Profile、MCP 描述和 ACP runner，只读。
- `EVAL_MODEL`、`EVAL_MODEL_PROVIDER`、`EVAL_WIRE_API`、`EVAL_REASONING_EFFORT`：本次 Run 的模型决议。
- 资源限制：read-only root、tmpfs、drop capabilities、no-new-privileges、memory、PIDs。

Windows Blender/UE5 不进入这个容器。它们仍在宿主机运行，Agent 通过本次 Run 注入的标准 MCP 地址访问它们。

### Codex

`profiles/agents/codex*.yaml` 选择 Codex 镜像和 `CodexAdapter` 规则。镜像 entrypoint 使用本次 Run 的工作目录、MCP 文件和 trace 路径；不读取宿主 `~/.codex`，不改其他 Codex 会话配置。

### DSH

DSH Agent 可以选择 `profiles/agents/dsh.yaml`（本地源码）或 `profiles/agents/dsh-release.yaml`（发布包）。源码镜像由一个外部 DSH 源仓库构建，该仓库位置由本机 `config/eval.yaml` 指定，不在本仓库内：

```text
<DSH source repository>          # 位置见 config/eval.yaml
    ↓ build
ai-native-dsh-agent:local
    ↓ run in Docker
in-container ACP runner
    ↓ standard JSON-RPC
DSH --profile acp
```

ACP runner 为这次容器生成独立 `DSH_HOME` 和 `settings.yaml`，将 `EVAL_GATEWAY_URL` / `EVAL_GATEWAY_API_KEY` 作为 DSH 的 OpenAI Responses provider，并把 `EVAL_DSH_MCP_SERVERS_FILE` 中的标准 ACP `McpServer[]` 传给 `session/new`。

## Wait：保存过程和统一事件

Agent 原始输出永远保留：

```text
trace/agent-container.log       # Docker 原始输出
trace/normalized-events.jsonl   # 统一事件
```

Codex JSONL 与 DSH ACP 都转成：

```text
run_started / run_completed
turn_started / turn_completed
agent_message / reasoning
tool_call / tool_result
command_started / command_completed
file_changed / approval_requested / error
```

原始协议用于故障诊断；统一事件用于 Process Scorer、Digest、Inspect Transcript 和未来 Viewer。换 Agent 不需要重写过程评分。

## Evaluate：三种 Check

```text
TestPlan.ordered_checks()
        │
        ├── Outcome
        │     Locator Agent（只读 Sandbox）
        │       ↓ selected_artifact
        │     脚本/主机状态验证
        │
        ├── Quality
        │     Judge Agent（只读 Sandbox）+ Task Rubric
        │
        └── Process
              normalized-events + digest
```

Check 通过 `depends_on` 组成 DAG。硬 Outcome Check 失败时最终 decision 为 `fail`；Quality 低于阈值也可失败；Process 默认只做诊断，不掩盖客观结果。

所有 Agent-backed Evaluator 都复用同一个 Sandbox 机制，但每个角色获得自己的临时容器、网络、trace 和权限；它们不会共享可写容器。

## 清理和复现

在 `cleanup` 前保留至少以下文件用于复盘：

```text
run-manifest.json
evidence/evaluation.json
evidence/checks/*.json
trace/agent-container.log
trace/normalized-events.jsonl
trace/digest.json
```

同一 Task 对 Codex 与 DSH 进行公平对比时，必须保持：Task Prompt、资源/Fixture、MCP、超时、Sandbox 限制、TestPlan、Rubric 和模型不变，只替换 `--agent`。
