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
- [AI-Native Eval Console 产品与交互规格](docs/EVAL_CONSOLE_SPEC.md)
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
profiles/models/        Model Binding（Provider + Model + 协议/推理强度）
profiles/mcp/           MCP/宿主服务 Profile
profiles/sandboxes/     Docker/WSL Sandbox Profile
config/presets/         常用运行组合（只绑定 Profile id）
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
apps/eval-console/      React + TypeScript 可视化 Console
src/ai_native_evals_console/  Console Read Model、Catalog、API
```

## 5 分钟上手

### 一条命令

```powershell
.\tools\eval.ps1 setup      # 安装 Python + 前端依赖
.\tools\eval.ps1 check      # 体检：仓库接线 + 这台机器
.\tools\eval.ps1 build      # 构建镜像（需要时）
.\tools\eval.ps1 test       # 跑测试与 lint
```

`check` 是**跑之前该做的事**：它先验证仓库接线（`doctor`），再验证这台机器
（`preflight`），最后给出「能不能跑」的结论。

```text
=== Repository wiring ===      profile 能否解析、Task 是否存在
=== Machine environment ===    工具链 / Docker / 凭据 / 镜像 / MCP host
=== Summary ===
  repository wiring      ok
  machine environment    ok
  This machine can run an evaluation.
```

带 Task 时会额外检查该 Task 需要的镜像与 MCP host：

```powershell
.\tools\eval.ps1 check -Task codex-file-smoke -Preset codex-default
```

### 等价的分步命令

```powershell
cd <path-to-AI-Native-Evals>

# 依赖：Python 侧 + 前端侧
uv sync --dev
pnpm install                 # 安装 Console 依赖（workspace 根命令）

# 环境体检：这台机器到底能不能跑
uv run ai-native-evals preflight                    # 工具链 / Docker / 凭据
uv run ai-native-evals preflight codex-file-smoke   # 再加上该 Task 需要的镜像与 MCP host

# 发现当前定义
uv run ai-native-evals task list
uv run ai-native-evals agent list
uv run ai-native-evals model list
uv run ai-native-evals mcp list
uv run ai-native-evals sandbox list
uv run ai-native-evals preset list
uv run ai-native-evals config show
uv run ai-native-evals doctor

# Provider 与它实际提供的模型（实时询问上游 /v1/models）
uv run ai-native-evals provider list
uv run ai-native-evals provider models sub2api
uv run ai-native-evals provider models sub2api --reasoning   # 逐模型探测合法 reasoning 等级

# 验证一个 Task（不创建 Run）
uv run ai-native-evals task validate codex-file-smoke
uv run ai-native-evals run plan codex-file-smoke

# Inspect wiring smoke（不调用真实模型）
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model

# 构建并启动 AI-Native Eval Console
pnpm build
uv run ai-native-evals console
# 或使用一键脚本：.\tools\console.ps1
# 打开 http://127.0.0.1:8787/
```

### 跑之前先体检

`doctor` 检查**仓库接线**（profile 能否解析、Task 是否存在）；`preflight`
检查**这台机器**（工具链、Docker、凭据、镜像、MCP host）。两者都要通过。

```powershell
uv run ai-native-evals preflight --json      # 机器可读
```

每一项有三种状态，区别很重要：

```text
ok       已验证可用
missing  已确认缺失 —— 会阻止运行
unknown  无法判定 —— 不阻止运行
```

`unknown` 不算失败：**探测不到不等于东西不存在**。Docker 没开、WSL distro
名字不对，都会如实报 `unknown` 或 `missing` 并给出修复命令，而不是把一台好
机器判成坏的。

`run execute` 会自动先跑一次 preflight，不通过就拒绝启动（省掉一次注定失败的
Docker 启动）；确实要强行启动时用 `--skip-preflight`。


## AI-Native Eval Console

Console 是本项目唯一的正式可视化前端，读取仓库外的 `EvalRuns`，不读取或修改 Agent 的全局配置。Inspect Viewer 仍只作为 Inspect 原始日志兼容工具，不是本项目的标准入口。

```powershell
pnpm install     # 安装 workspace 依赖（根目录一条命令即可）
pnpm build
uv run ai-native-evals console
# 一键启动：.\tools\console.ps1
```

在 Console 的 `Runs` 页面点击“运行测试”，选择已有的 Task、Agent、Model、MCP、Sandbox 或 Preset。Console 会先展示 Run Plan，确认后才启动真实 Docker 评测。

### 前端开发

仓库根目录的 `package.json` 是 workspace 入口，转发到 `apps/eval-console`：

```powershell
pnpm install     # 安装依赖（必须）
pnpm run dev     # Vite dev server -> http://127.0.0.1:5173/
pnpm run build   # 构建并同步到 src/ai_native_evals_console/static/
pnpm run test
```

`pnpm run dev` 需要 Console API 同时在跑，另开一个终端执行 `uv run ai-native-evals console`；
Vite 启动时会自动探测 API，不可达会直接给出提示。

> `apps/eval-console` 通过根目录的 `pnpm-workspace.yaml` 声明为 workspace 成员。
> 没有这个文件时，根目录的 `pnpm install` 会因为根 manifest 本身没有依赖而
> **报告成功却什么都不装**，直到 `pnpm build` 才失败。

> `node_modules` 中是 Windows 原生二进制，请在 Windows 侧（PowerShell / cmd）使用 pnpm；
> 在 WSL 中执行会报 `Cannot find native binding`。

完整的页面、数据契约、安全边界和验收标准见：[Console 产品与交互规格](docs/EVAL_CONSOLE_SPEC.md)。

## 定义 Task

可以先生成一个最小的一文件 Task：

```powershell
uv run ai-native-evals task new my-task
```

然后只编辑：

```text
tasks\my-task\task.yaml
```

新增：

```text
tasks/my-task/
├── task.yaml             # 小 Task 可以把 Prompt/Rubric 直接写在这里
├── prompt.md             # 可选：Prompt 较长时拆出
├── dataset.jsonl         # 可选：多样本
└── rubric.yaml           # 可选：需要 Quality Judge 时使用
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
uv run ai-native-evals compare my-task --agents codex,dsh-release --preset codex-default
```

这两次运行复用相同的 Prompt、资源、MCP、超时、TestPlan 和 Rubric，只替换被测 Profile；Outcome Locator/Quality Judge 默认仍由 `codex` Profile 执行。底层仍通过 `profiles/models/*.yaml` 的 Model Binding 解析；Console 将 Provider 和 Model 分开展示和选择，再映射到对应 Binding，不会修改本机其他 Codex 会话配置。

## Agent 接入

### Codex

`profiles/agents/codex*.yaml` 选择 Codex Docker 镜像，`CodexAdapter` 保存 Codex JSONL 并生成统一 Trace。

### DSH

`profiles/agents/dsh.yaml`（本地源码）从一个外部 DSH 源仓库构建 `ai-native-dsh-agent:local`，该仓库位置由本机 `config/eval.yaml` 指定；`profiles/agents/dsh-release.yaml` 使用发布的 DSH CLI 构建快速兼容镜像。容器内的 ACP runner 通过标准 ACP JSON-RPC 驱动 `dsh --profile acp`；MCP 使用标准 ACP `McpServer[]`，不使用 `mcp_socket_call.py` 或自定义 Blender/UE5 socket adapter。

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
uv run ai-native-evals run execute codex-file-smoke --preset codex-default
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
