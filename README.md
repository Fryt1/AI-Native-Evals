# AI-Native-Evals

基于 [Inspect AI](https://inspect.aisi.org.uk/) 的、可插拔 Agent 评测框架，用来在同一个 Task/TestPlan 上比较 Codex、DeepSeek Harness（DSH）以及未来的其他 Agent。

> 本仓库不是 Inspect AI 的 fork。Inspect 负责标准的 Task、Dataset、Solver、Scorer、日志和 Viewer；本仓库只实现 Agent 接入、Run Workspace、Docker 隔离、领域验收和统一 Trace。

## 核心架构

```text
Task Bundle + TestPlan
        │
        ▼
Run Resolver ──► immutable run-manifest.json
        │
        ▼
Task Resource Providers ──► per-run Workspace
        │
        ▼
SandboxRuntime (Docker/WSL)
        │
        ├── Agent Profile ── Agent Adapter ── Codex / DSH / future Agent
        ├── raw trace
        └── normalized AgentEvent[]
        │
        ▼
Outcome / Quality / Process Evaluators
        │
        ▼
EvaluationReport + Inspect Transcript/Viewer
```

完整解释：

- [架构说明](docs/ARCHITECTURE.md)
- [项目与 Task 编写指南](docs/PROJECT_GUIDE_CN.md)
- [Run 生命周期](docs/RUN_LIFECYCLE.md)
- [评测管线](docs/EVALUATION_PIPELINE.md)
- [TestPlan 语法](docs/TEST_PLANS.md)
- [离线缓存](docs/OFFLINE_SANDBOX_CACHE.md)
- [Codex / DSH 同 Task 实跑结果](docs/CODEX_DSH_COMPARISON.md)

## 目录

```text
config/                 全局路径、默认值、Sandbox 限制、MCP registry
profiles/agents/        Codex/DSH 等 Agent Profile
profiles/models/        模型/Provider/协议/推理强度 Profile
tasks/<id>/             Task Bundle：task.yaml + prompt.md + rubric.yaml
prompts/                跨 Task 复用的 Locator/Judge Prompt
src/ai_native_evals/
├── agents/             AgentAdapter、Profile、Registry
├── adapters/           Codex JSONL、DSH ACP、统一 Trace、Inspect 投影
├── resources/          Task 资源声明与快照 Provider
├── evaluation/         TestPlan Runner 和 EvaluationReport
├── scorers/            Inspect Scorer/领域验收适配
├── solvers/            通用 agent_solver 与兼容 Solver
└── runs/               Workspace、Docker、生命周期和 Evaluator Sandbox
docker/                 Codex/DSH Agent 镜像定义
tools/                  镜像、宿主 DCC、缓存和诊断脚本
EvalRuns/               仓库外运行现场，不提交
```

## 5 分钟上手

```powershell
cd D:\work\AI-Native\AI-Native-Evals
uv sync --dev

# 发现当前定义
uv run ai-native-evals task list
uv run ai-native-evals agent list
uv run ai-native-evals model list
uv run ai-native-evals doctor

# 验证一个 Task（不创建 Run）
uv run ai-native-evals task validate structured-report-contract
uv run ai-native-evals run plan structured-report-contract

# Inspect wiring smoke（不调用真实模型）
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
```

## 定义 Task

新增：

```text
tasks/my-task/
├── task.yaml
├── prompt.md
└── rubric.yaml       # 需要 Quality Judge 时才需要
```

最小 `task.yaml`：

```yaml
id: my-task
version: 1
prompt_file: prompt.md
resources: []
execution:
  mcp_profile: none
test_plan:
  version: 1
  checks:
    - id: result-file
      phase: outcome
      evaluator: script.file_exists.v1
      input:
        path: /workspace/output/result.json
      required: true
```

Task 没有声明 `game-engine` 就不会复制 Game Engine。需要项目时显式声明：

```yaml
resources:
  - id: game-engine
    kind: repository
    source: game_engine
    mount: game-engine
```

同一个 Task 对比 Agent：

```powershell
uv run ai-native-evals run execute my-task --agent codex
uv run ai-native-evals run execute my-task --agent dsh
```

这两次运行复用相同的 Prompt、资源、MCP、超时、TestPlan 和 Rubric，只替换被测 Profile；Outcome Locator/Quality Judge 默认仍由 `codex` Profile 执行。模型通过 `--model-profile` 或 `profiles/models/*.yaml` 选择，不会修改本机其他 Codex 会话配置。

## Agent 接入

### Codex

`profiles/agents/codex*.yaml` 选择 Codex Docker 镜像，`CodexAdapter` 保存 Codex JSONL 并生成统一 Trace。

### DSH

`profiles/agents/dsh.yaml`（本地源码）使用真正的 `D:\work\AI-Native\dsh` 源仓库构建 `ai-native-dsh-agent:local`；`profiles/agents/dsh-release.yaml` 使用发布的 DSH CLI 构建快速兼容镜像。容器内的 ACP runner 通过标准 ACP JSON-RPC 驱动 `dsh --profile acp`；MCP 使用标准 ACP `McpServer[]`，不使用 `mcp_socket_call.py` 或自定义 Blender/UE5 socket adapter。

构建：

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDshRelease -UseMirror
# 需要严格复现本地 dsh commit 时：
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDsh -UseMirror
```

只改 `profiles/agents` 或新增 Adapter，不要在共享 Evaluator 里写 `if codex / if dsh`。

## Docker 与 Windows DCC

Agent 必须在每次 Run 的 Docker Sandbox 中运行；Windows Blender、UE5、ComfyUI 保留在宿主机。容器通过本次 Run 的标准 MCP 配置访问宿主服务：

```text
Docker Agent
    └── standard MCP
            ├── Blender host service
            ├── UE5 streamable HTTP MCP
            └── other host services
```

每次 Run 有独立 network、gateway、Agent container、workspace 和 trace。评测 Agent（Outcome Locator / Quality Judge）也在独立只读容器中复用同一个 Workspace。

## 查看一次运行

```powershell
uv run ai-native-evals run execute structured-report-contract --agent codex
uv run ai-native-evals run status <run-id>
uv run ai-native-evals run digest <run-id>
```

现场：

```text
EvalRuns/<run-id>/
├── run-manifest.json
└── workspace/
    ├── output/                 被测 Agent 的真实产物
    ├── evidence/checks/        每个 Check 的事实、分数和错误
    ├── evidence/evaluation.json
    ├── trace/agent-container.log
    ├── trace/normalized-events.jsonl
    ├── trace/digest.json
    └── trace/evaluators/       Locator/Judge 的独立过程
```

判定不依赖 Agent 最后一句话：

- Outcome：Locator 找到真实产物，脚本/宿主读回决定客观是否完成。
- Quality：独立 Judge 按 Task Rubric 打分，只读 Workspace。
- Process：统一 Trace 的工具调用、失败、重试、验证前置等诊断指标。

## 开发验证

```powershell
uv run pytest
uv run ruff check src tests
node --check .\docker\dsh-agent\acp-runner.mjs
```

## 秘密和运行现场

不要提交：

```text
config/.env.local
EvalRuns/
cache/codex/*.tgz
cache/docker/*.tar
cache/python/*/wheels/*.whl
API key / token / 个人 DSH_HOME
```

核心原则：

> **Task 决定测什么，Profile 决定用谁，Adapter 决定怎么接，Sandbox 决定在哪里跑，Evaluator 决定怎么验，Workspace 保存事实。**
