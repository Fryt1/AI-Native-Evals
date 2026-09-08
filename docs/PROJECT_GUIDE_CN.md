# AI-Native-Evals：项目结构与定义一个 Task

本文对应当前实现（**2026 年 9 月 8 日**），重点回答两个问题：

1. 每个目录到底负责什么？
2. 新增一个测试 Task 时，具体写哪些文件、执行什么命令？

## 一、仓库之间的关系

```text
D:\work\AI-Native\
├── AI-Native-Evals       # 评测框架 + Task + Adapter + Evaluator
├── AI-Native-Game-Engine # 被测项目源仓库（只有 Task 声明时才快照）
├── AI-Native-DSH         # 我们的 Game Engine/DSH 插件源（按 Task 声明才快照）
├── dsh                   # 真正的 DeepSeek Harness 源仓库，用来构建 DSH Agent 镜像
└── EvalRuns              # 仓库外，每次运行的完整现场
```

`AI-Native-Evals` 不把其他仓库 import 成评测框架的一部分。它只在一次 Run 准备时，根据 Task 的 `resources` 声明创建快照；没有声明就不复制。

## 二、目录职责

```text
AI-Native-Evals/
├── config/
│   ├── eval.yaml          # 全局默认、路径别名、Sandbox 限制、MCP registry
│   ├── .env.example       # 秘密配置示例
│   └── .env.local         # 本机秘密；被 gitignore，不能提交
│
├── profiles/
│   ├── agents/            # Codex、DSH 等 Agent 的启动 profile
│   └── models/            # 模型、provider、协议、推理强度
│
├── tasks/
│   └── <task-id>/
│       ├── task.yaml      # 资源、TestPlan、默认 MCP/Agent 偏好
│       ├── dataset.jsonl  # 可选：多样本数据集
│       ├── prompt.md      # 给被测 Agent 的 Prompt
│       └── rubric.yaml    # 该 Task 的 Quality Rubric（可选）
│
├── prompts/
│   ├── outcome/           # 跨 Task 复用的 Outcome Locator Prompt
│   ├── quality/           # 跨 Task 复用的 Quality Judge Prompt
│   └── process/           # Process 分析模板（可选）
│
├── src/ai_native_evals/
│   ├── agents/            # AgentAdapter、AgentProfile、Registry
│   ├── adapters/          # Codex JSONL、DSH ACP、Trace、Inspect 投影
│   ├── resources/         # ResourceProvider 和快照实现
│   ├── evaluation/        # TestPlan Runner、Check、EvaluationReport
│   ├── scorers/           # Inspect Scorer 和领域验收适配
│   ├── solvers/           # Inspect Solver；agent_solver 支持多 Agent
│   ├── sandboxes/         # Windows DCC seam 和 Workspace 描述
│   └── runs/              # Run manifest、Docker lifecycle、Evaluator Sandbox
│
├── docker/
│   ├── codex-agent/       # Codex Agent 镜像和 entrypoint
│   └── dsh-agent/         # DSH ACP 镜像、in-container ACP runner
│
├── tools/                 # 构建镜像、启动宿主 DCC、缓存、诊断脚本
├── fixtures/              # 可复现的初始输入
├── datasets/              # 数据集说明/未来批量样本
├── tests/                 # 契约、Adapter、Runner、Task 测试
└── docs/                  # 架构、生命周期、编写和运维文档
```

## 三、中央配置与 Task Bundle 的分工

以前所有内容都塞在 `config/eval.yaml`。现在 `eval.yaml` 只放全局内容：

```yaml
paths:
  source_roots:
    game_engine: ../game-engine
    dsh: ../AI-Native-DSH
    dsh_runtime: ../dsh
  runs_root: ../EvalRuns

defaults:
  agent: codex
  model_profile: sub2api-deepseek
  mcp_profile: none

profile_roots:
  agents: profiles/agents
  models: profiles/models
```

Task 自己放在 `tasks/<id>/`。加载优先级是：

```text
tasks/<id>/task.yaml + prompt.md + rubric.yaml
    ↓ 如果不存在
config/eval.yaml 中的旧 tasks.<id>
```

旧格式仍兼容，便于迁移；新 Task 不应再写进中央 `tasks:`。

## 四、如何定义一个最小 Task

创建目录：

```powershell
New-Item -ItemType Directory .\tasks\my-task
```

### 1. `prompt.md`

只写被测 Agent 应该知道的目标，不写隐藏答案或评分实现：

```markdown
Create `/workspace/output/result.json`.
It must be valid UTF-8 JSON and contain a non-empty `summary`.
Read the file back before reporting completion.
```

### 2. `task.yaml`

```yaml
id: my-task
version: 1
prompt_file: prompt.md

# 没有项目依赖时就是 []；不会自动复制 Game Engine。
resources: []

# execution 只提供默认值，命令行可以覆盖 agent/model。
execution:
  mcp_profile: none

test_plan:
  version: 1
  checks:
    - id: locate-result
      phase: outcome
      evaluator: agent.artifact_locator.v1
      input:
        roots: [/workspace/output]
      config:
        prompt: prompts/outcome/artifact-locator-v1.md
        artifact_kind: json_document
        expected_name: result.json
      required: true
      on_error: fail

    - id: validate-result
      phase: outcome
      evaluator: script.json_contract.v1
      input:
        artifact: locate-result.selected_artifact
      config:
        required_fields: [summary]
        field_types: {summary: string}
        non_empty_fields: [summary]
      required: true
      depends_on: [locate-result]

    - id: result-quality
      phase: quality
      evaluator: agent.quality_judge.v1
      input:
        artifact: validate-result.path
      config:
        prompt: prompts/quality/text-artifact-v1.md
        rubric: rubric.yaml
      required: false
      on_error: review
      depends_on: [validate-result]

    - id: process-observation
      phase: process
      evaluator: trace.process_analyzer.v1
      required: false
      on_error: review
```

### 3. `rubric.yaml`（需要主观质量时）

```yaml
criteria:
  - id: completeness
    description: The artifact contains the requested information.
    weight: 0.6
  - id: clarity
    description: The result is clear and internally coherent.
    weight: 0.4
passing_score: 0.8
```

## 五、Task 需要项目时如何声明资源

资源是 Task 的输入，不是评测框架的默认依赖：

```yaml
resources:
  - id: game-engine
    kind: repository
    source: game_engine
    mount: game-engine
    # 可选：ref: main

  - id: starter-fixture
    kind: fixture
    source: fixtures/my-task
    mount: fixtures/starter
```

类型含义：

| kind | 行为 |
|---|---|
| `repository` | Git ref 优先使用 `git archive`；无 ref 时复制 working tree 并排除运行产物 |
| `directory` | 复制普通目录 |
| `fixture` | 复制可复现输入目录 |
| `host_service` | 不复制，只记录宿主服务信息；适合 Blender/UE5/MCP 服务 |

对于 Blender/UE5，DCC 程序留在 Windows 主机；Task 可以声明 `game-engine` 快照，Agent 容器通过标准 MCP 访问运行中的宿主 DCC。

## 六、如何切换 Agent / 模型

Agent Profile：

```text
profiles/agents/codex.yaml
profiles/agents/codex-dcc.yaml
`profiles/agents/dsh.yaml`（本地源码）或 `profiles/agents/dsh-release.yaml`（发布包）
```

模型 Profile：

```text
profiles/models/sub2api-deepseek.yaml
```

同一 Task 对比：

```powershell
uv run ai-native-evals run execute my-task --agent codex --model-profile sub2api-deepseek
uv run ai-native-evals run execute my-task --agent dsh --model-profile sub2api-deepseek
```

这不会改本机其他 Codex 会话的模型配置。每次 Run 都把 Profile 和模型决议写入自己的 `run-manifest.json`，Docker 只拿到这次 Run 的配置。

## 七、执行与查看

先做静态检查：

```powershell
uv run ai-native-evals task list
uv run ai-native-evals task show my-task
uv run ai-native-evals task validate my-task
uv run ai-native-evals agent list
uv run ai-native-evals model list
uv run ai-native-evals doctor
```

只解析 TestPlan，不创建 Run：

```powershell
uv run ai-native-evals run plan my-task
```

完整执行：

```powershell
uv run ai-native-evals run execute my-task --agent codex
```

分步执行：

```powershell
uv run ai-native-evals run prepare my-task --agent codex
uv run ai-native-evals run status <run-id>
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run logs <run-id>
uv run ai-native-evals run wait <run-id>
uv run ai-native-evals run evaluate <run-id>
uv run ai-native-evals run digest <run-id>
```

## 八、一次 Run 产生什么

```text
D:\work\AI-Native\EvalRuns\<run-id>\
├── run-manifest.json
└── workspace/
    ├── game-engine/          # 只有 Task 声明 resource 才出现
    ├── ai-native-dsh/        # 只有 Task 声明 dsh resource 才出现
    ├── output/               # 被测 Agent 的候选产物
    ├── scratch/              # 临时工作
    ├── artifacts/            # 中间/导出物
    ├── evidence/
    │   ├── checks/<id>.json  # 每个 Check 的事实和错误
    │   ├── evaluation.json   # Outcome/Quality/Process 聚合
    │   └── verdict.json      # 兼容入口
    ├── trace/
    │   ├── agent-container.log
    │   ├── normalized-events.jsonl
    │   ├── digest.json
    │   ├── digest.md
    │   └── evaluators/<role>/<nonce>/
    └── agent-config/
        ├── agent-profile.json
        ├── mcp-servers.json
        ├── dsh-mcp-servers.json
        └── dsh-acp-runner.mjs
```

### 判断 Agent 好坏看什么

- **Outcome**：是否留下真实产物、产物是否满足硬契约；不信最终文字汇报。
- **Quality**：独立 Judge 依据 Task Rubric 读取产物和证据；只读，不改现场。
- **Process**：查看统一事件序列、工具失败、重试、验证前置情况；默认是诊断分，不覆盖硬结果。

## 九、提交与不提交

提交：

```text
profiles/
tasks/
prompts/
src/
tests/
docs/
docker/（Dockerfile 和脚本）
tools/
config/eval.yaml
```

不提交：

```text
config/.env.local
EvalRuns/
cache/codex/*.tgz
cache/docker/*.tar
cache/python/*/wheels/*.whl
任何 API key、token、个人 DSH home
```

核心规则：

> **Task 决定测什么，Profile 决定用谁，Adapter 决定怎么接，Sandbox 决定在哪里跑，Evaluator 决定怎么验，Workspace 保存事实。**
