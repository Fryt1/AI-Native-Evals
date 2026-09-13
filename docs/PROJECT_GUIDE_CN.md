# AI-Native-Evals：项目结构与定义一个 Task

本文对应当前实现（**2026 年 9 月 8 日**），重点回答两个问题：

1. 每个目录到底负责什么？
2. 新增一个测试 Task 时，具体写哪些文件、执行什么命令？

## 一、与外部仓库的关系

本仓库是一个**独立的评测框架**。它不 import 任何被测项目的代码，也不假设
被测仓库位于什么位置；两者之间只有一条运行时的数据通道：

```text
Task 声明 resources  →  运行时创建快照  →  Agent 在容器内看到挂载目录
```

三点约束：

1. **没有声明就不复制。** Task 写 `resources: []` 时，workspace 里不会有任何
   被测项目目录，评测框架也不会去查找它。
2. **位置由本机配置决定，不写死在代码里。** 源仓库位置来自
   `config/eval.yaml` 的 `paths.source_roots`（可指向任意路径），不是固定布局。
3. **只有被快照的那一份参与评测。** 快照在 Run 准备时创建，之后即使源仓库
   变化，该 Run 的记录也不会变。

被测项目的种类、数量、位置都属于**本机部署细节**，因此不出现在本文档中。
要看当前这台机器解析到了哪些源仓库：

```powershell
uv run ai-native-evals config show
```

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
│   ├── providers/         # 上游：base_url 凭据引用、协议、模型显示名
│   └── models/            # 历史 model binding（provider+model+协议+推理）
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
  # 被测源仓库的位置：本机部署细节，写在 config/eval.local.yaml（被 gitignore）；
  # 被跟踪的 eval.yaml 里保持为空 {}，因此仓库可以直接共享。
  source_roots:
    <resource-source-id>: <path-to-repository>
  # 运行现场；通常在仓库外，且不进 git。
  runs_root: <path-to-runs-directory>

defaults:
  agent: codex
  provider: sub2api          # 上游名称，见 profiles/providers/
  mcp_profile: none

profile_roots:
  agents: profiles/agents
  models: profiles/models
  mcp: profiles/mcp
  sandboxes: profiles/sandboxes
  presets: config/presets
```

`config/eval.yaml` 是**被跟踪**的，只描述框架本身，不含任何宿主路径。本机路径
写在 `config/eval.local.yaml`（被 gitignore），它按 key 覆盖前者：

```powershell
copy config\eval.local.example.yaml config\eval.local.yaml
# 再填入本机的仓库位置
```

`source_roots` 的键就是 Task 里 `resources[].source` 引用的名字。新增一个被测
项目只需在**本地**文件里加一条，并在 Task 中声明；框架本身不需要改代码，也不
需要知道这些仓库彼此的位置关系。

### 三种 source 形态

```yaml
source_roots:
  # 1. 本机目录（最快，不走网络）
  local_project: ../my-project

  # 2. 远程 Git（换台机器 / CI 也能跑）：首次 clone 到 cache/repos/，之后复用
  shared_project: https://github.com/org/repo.git

  # 3. 远程 + 默认 ref（Task 不必重复写）
  pinned_project:
    url: https://github.com/org/repo.git
    ref: v1.2.0
```

远程快照会被记录**解析到的 commit**，所以事后能回答"这次评测跑的是哪份代码"。

缓存**默认复用**，不会被远端 push 悄悄改变输入；要更新用：

```powershell
uv run ai-native-evals run execute my-task --refresh-sources
```

### CI 上不需要改任何文件

环境变量优先级高于配置文件：

```bash
AI_NATIVE_EVALS_SOURCE_MY_PROJECT=https://github.com/org/repo.git
AI_NATIVE_EVALS_SOURCE_REF_MY_PROJECT=v1.2.0    # 可选
```

CI 因此可以在不修改仓库的前提下，把逻辑 id 指向远程代码。若远端暂时不可达而
本地已有缓存，会**继续用缓存**（只有显式 `--refresh-sources` 失败才算失败）。


Task 自己放在 `tasks/<id>/`。加载优先级是：

```text
tasks/<id>/task.yaml + prompt.md + rubric.yaml
    ↓ 如果不存在
config/eval.yaml 中的旧 tasks.<id>
```

旧格式仍兼容，便于迁移；新 Task 不应再写进中央 `tasks:`。

常用运行组合放在 `config/presets/`，只写 Profile id，不复制详细配置：

```yaml
id: dsh-release
agent: dsh-release
provider: sub2api
model: deepseek/deepseek-v4.1-flash
reasoning_effort: high
mcp_profile: none
sandbox_profile: docker-default
```

## 四、如何定义一个最小 Task

先用 CLI 生成模板：

```powershell
uv run ai-native-evals task new my-task
```

这会创建 `tasks/my-task/task.yaml`。小 Task 可以只维护这一份文件；Prompt 较长、多样本或 Rubric 较复杂时，再拆成 `prompt.md`、`dataset.jsonl` 和 `rubric.yaml`。

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
  - id: my-project            # 本 Task 内的标识
    kind: repository
    source: my_project_source # 对应 config/eval.yaml 的 paths.source_roots 键
    mount: my-project         # 容器内 /workspace/<mount> 的名字
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

对于 Blender/UE5，DCC 程序留在 Windows 主机；Task 可以声明一个项目快照作为
Agent 的工作目录，Agent 容器通过标准 MCP 访问运行中的宿主 DCC。

## 六、如何切换 Agent / 模型

Agent Profile：

```text
profiles/agents/codex.yaml
profiles/agents/codex-dcc.yaml
`profiles/agents/dsh.yaml`（本地源码）或 `profiles/agents/dsh-release.yaml`（发布包）
```

模型 Profile：

```text
profiles/providers/sub2api.yaml
```

同一 Task 对比：

```powershell
uv run ai-native-evals run execute my-task --agent codex --provider sub2api --model gpt-5.6-luna --reasoning-effort high
uv run ai-native-evals run execute my-task --agent dsh   --provider sub2api --model gpt-5.6-luna --reasoning-effort high
```

`--provider` 选定上游，`--model` 必须是该上游 `/v1/models` 实际列出的模型，`--reasoning-effort` 必须是该 Provider 与该 Agent 都接受的等级。三者任一不合法都会在启动 Docker 前报错。历史写法 `--model-profile <binding>` 仍然可用。

这不会改本机其他 Codex 会话的模型配置。每次 Run 都把 Profile 和模型决议写入自己的 `run-manifest.json`，Docker 只拿到这次 Run 的配置。

## 七、执行与查看

先做静态检查：

```powershell
uv run ai-native-evals task list
uv run ai-native-evals task show my-task
uv run ai-native-evals task validate my-task
uv run ai-native-evals agent list
uv run ai-native-evals model list
uv run ai-native-evals provider list
uv run ai-native-evals provider models sub2api
uv run ai-native-evals doctor
```

`doctor` 看**仓库接线**，`preflight` 看**这台机器**：

```powershell
uv run ai-native-evals preflight my-task     # 工具链 + Docker + 凭据 + 镜像 + MCP host
```

每项状态为 `ok` / `missing` / `unknown`。`missing` 会阻止运行，`unknown`
不会 —— **探测不到不等于东西不存在**。`run execute` 会自动先跑 preflight。

`provider list` 只读本地配置、不联网；`provider models <id>` 实时询问上游
`/v1/models`，因此它就是"现在到底有哪些模型可用"的权威答案。选出模型后即可
直接用于 Run，不必先建 model binding：

```powershell
uv run ai-native-evals run plan my-task --provider sub2api --model gpt-5.6-luna
```

只解析 TestPlan，不创建 Run：

```powershell
uv run ai-native-evals run plan my-task
```

完整执行：

```powershell
uv run ai-native-evals run execute my-task --preset codex-default
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
<EvalRuns>/<run-id>/
├── run-manifest.json
└── workspace/
    ├── <mount>/              # 每个 Task 声明的 resource 一个目录；没声明就没有
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
