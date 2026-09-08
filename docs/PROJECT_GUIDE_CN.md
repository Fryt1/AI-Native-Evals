# AI-Native-Evals 项目与 Task 编写指南

本文面向第一次使用 `AI-Native-Evals` 的开发者，说明项目中每个目录的职责，以及如何从零定义并执行一个 Task。

## 1. 项目实际位置

当前工作区的实际结构是：

```text
D:\work\AI-Native\
├── AI-Native-Evals\       # 本评测框架 Git 仓库
├── AI-Native-Game-Engine\ # 被 Agent 读取/修改的项目源仓库
├── AI-Native-DSH\         # DSH Agent 源仓库
└── EvalRuns\              # 每次评测生成的运行现场（仓库外）
```

因此 `EvalRuns` 在逻辑上属于评测系统，但当前物理位置是：

```text
D:\work\AI-Native\EvalRuns\
```

不是：

```text
D:\work\AI-Native\AI-Native-Evals\EvalRuns\
```

这个路径由 `config/eval.yaml` 中的 `paths.runs_root` 配置：

```yaml
paths:
  runs_root: ../EvalRuns
```

## 2. 仓库目录总览

```text
AI-Native-Evals/
├── config/
│   ├── eval.yaml
│   ├── rubrics/
│   └── .env.local
│
├── prompts/
│   ├── outcome/
│   └── quality/
│
├── src/ai_native_evals/
│   ├── tasks/
│   ├── evaluation/
│   ├── scorers/
│   ├── solvers/
│   ├── adapters/
│   └── runs/
│
├── docker/
├── tools/
├── fixtures/
├── datasets/
├── experiments/
├── vendor/
├── tests/
└── docs/
```

下面按“写 Task 时是否会改”说明每一部分。

## 3. `config/`：运行配置和任务声明

### `config/eval.yaml`

这是当前 CLI 评测流程的**中央配置文件**。它负责描述：

```text
项目路径
运行目录
Sandbox 默认限制
Agent 镜像
模型 Profile
MCP Profile
Task Prompt
TestPlan
Check 配置
```

文件结构大致是：

```yaml
paths:          # 源仓库和 EvalRuns 路径
sandbox:        # Docker/WSL 默认限制
defaults:       # 默认 Agent、模型、MCP、快照策略
model_profiles: # 模型、协议、推理强度
agents:         # Agent → Docker 镜像
mcp_profiles:   # MCP 能力集合
tasks:          # Task Prompt + TestPlan
```

定义新 Task 时，当前主要修改这里：

```yaml
tasks:
  my-task:
    agent: codex
    mcp_profile: none
    prompt: >-
      ...
    test_plan:
      ...
```

### `config/rubrics/`

存放 Quality Judge 使用的评分标准，不存放被测 Agent 的任务指令。

例如：

```text
config/rubrics/hello-world.yaml
config/rubrics/structured-report.yaml
```

Rubric 负责描述：

```text
评哪些标准
每项标准的权重
通过阈值
```

示例：

```yaml
criteria:
  - id: completeness
    description: The result includes all required information.
    weight: 0.5

  - id: clarity
    description: The result is clear and internally coherent.
    weight: 0.5

passing_score: 0.8
```

### `config/.env.local`

存放本机秘密，例如：

```text
API key
Gateway credential
Sub2API 配置
```

它不会提交到 Git，也不会写入 `cache/manifest.json`。不要把秘密写进：

```text
config/eval.yaml
Task Prompt
Rubric
Dockerfile
```

## 4. `prompts/`：评测 Agent 的固定提示词

这里存放评测阶段使用的提示词，不是被测 Task 的主要 Prompt。

### `prompts/outcome/`

Outcome Locator Agent 使用的提示词。

当前文件：

```text
prompts/outcome/artifact-locator-v1.md
```

它告诉独立 Locator Agent：

```text
只能读 Workspace
寻找候选产物
不能修改产物
遇到多个候选要报告 review
输出结构化 JSON
```

### `prompts/quality/`

Quality Judge Agent 使用的提示词。

当前文件：

```text
prompts/quality/text-artifact-v1.md
prompts/quality/structured-report-v1.md
```

它告诉 Quality Judge：

```text
以什么身份评分
可以查看什么证据
不能相信被测 Agent 的自述
如何使用 Rubric
必须输出什么 JSON
```

Task 通过 Check 的配置引用它：

```yaml
config:
  prompt: prompts/quality/structured-report-v1.md
  rubric: config/rubrics/structured-report.yaml
```

## 5. `src/ai_native_evals/`：框架实现

### `tasks/`

存放 Inspect AI 的 Python Task 定义：

```text
src/ai_native_evals/tasks/
├── smoke.py
├── codex_file_smoke.py
└── multi_dcc_roundtrip.py
```

这里主要用于：

```text
Inspect Task
Dataset
Solver
Inspect Scorer
Inspect Viewer
```

例如：

```python
@task
def codex_file_smoke() -> Task:
    return Task(
        dataset=MemoryDataset([...]),
        solver=codex_agent(...),
        scorer=hello_world_scorer(...),
    )
```

**当前注意：**

```text
config/eval.yaml 的 Task
    → ai-native-evals run CLI 的真实 Docker 流程

src/ai_native_evals/tasks/*.py 的 Task
    → Inspect AI 的 Python Task/Viewer 流程
```

目前两套入口并不是完全同一个文件生成的。新建真实沙箱任务时，优先使用 `config/eval.yaml`；如果还要通过 Inspect Viewer 运行，再增加对应的 Python Task 定义。

### `evaluation/`

这是当前声明式 TestPlan 的执行核心：

```text
src/ai_native_evals/evaluation/
├── contracts.py
├── runner.py
└── __init__.py
```

负责：

```text
读取 TestPlan
按 depends_on 排序
执行 Check
调用对应 Evaluator
保存 CheckResult
聚合 EvaluationReport
生成 evaluation.json / verdict.json
```

已有通用 Evaluator 在 `runner.py` 中注册：

```text
agent.artifact_locator.v1
agent.quality_judge.v1
script.file_exists.v1
script.text_equals.v1
script.json_contract.v1
script.blender_scene.v1
script.ue5_state.v1
trace.process_analyzer.v1
```

新增 Task 时，优先复用这些 Evaluator，只修改 `eval.yaml` 中的 `input` 和 `config`。

只有现有 Evaluator 无法表达新检测时，才新增：

```python
@register_evaluator("script.my_validator.v1")
def my_validator(context, check):
    ...
```

然后在 Task 中引用：

```yaml
evaluator: script.my_validator.v1
```

### `scorers/`

存放把评测结果接入 Inspect AI 的 Scorer 适配器，例如：

```text
hello_world.py
filesystem.py
multi_dcc_host_verifier.py
ainative.py
```

它们是 Inspect 的接口层。声明式 CLI TestPlan 的主要执行逻辑在 `evaluation/runner.py`，不要把每个 Task 的所有逻辑都塞进 Scorer。

### `solvers/`

存放被测 Agent 的启动适配器：

```text
Codex Solver
DSH Solver（后续）
其他 Agent Solver（后续）
```

这里负责“如何启动被测 Agent”，不负责定义 Task 的验收规则。

### `adapters/`

负责外部协议和日志转换，例如：

```text
Codex JSONL 事件解析
Agent 进程适配
run digest
```

### `runs/`

负责一次 Run 的生命周期和 Sandbox：

```text
lifecycle.py       # prepare、manifest、Workspace
resolver.py        # 解析 eval.yaml
spec.py            # RunSpec
snapshots.py       # 项目快照
 docker_runtime.py # 被测 Agent Docker
agent_sandbox.py   # Outcome/Quality 等评测 Agent Docker
```

一般新建 Task 不需要修改这里。

## 6. `docker/`：镜像构建定义

```text
docker/codex-agent/
├── Dockerfile
├── Dockerfile.sandbox
├── entrypoint.sh
└── render_codex_config.mjs
```

负责：

```text
构建 Codex Agent 镜像
安装 Blender MCP / Comfy MCP
配置隔离的 CODEX_HOME
渲染 run-scoped MCP 配置
```

新建 Task 通常不改 Dockerfile；只有需要新的系统依赖或 MCP 才修改这里。

## 7. `tools/`：操作脚本

当前主要脚本：

```text
tools/build-sandbox-images.ps1
    构建 Agent/Gateway 镜像

tools/prepare-offline-cache.ps1
    下载并保存 Docker、Python、Codex 构建输入

tools/verify-cache.ps1
    校验缓存文件和 SHA256

tools/start-eval-hosts.ps1
    启动评测用 Blender/UE5 MCP 服务

tools/stop-eval-hosts.ps1
    安全停止评测用宿主服务
```

它们是运行环境工具，不是 Task 定义。

## 8. `fixtures/`、`datasets/`、`experiments/`、`vendor/`

### `fixtures/`

可复现的初始输入环境，例如：

```text
fixtures/ue5/actor-fixture-mcp/
```

它用于让不同 Agent 在同一个初始状态下比较。

### `datasets/`

未来用于组织评测样本集：

```text
development
 guardian
holdout
challenge
```

一个 Task 可以有一个样本，也可以由 Dataset 提供很多样本变体。

### `experiments/`

记录实验条件，例如：

```text
Agent 版本
模型
推理强度
Prompt 版本
MCP Profile
```

### `vendor/`

保存固定版本的外部源码或构建输入，例如 Blender MCP 源码归档。它不是 Task 业务逻辑。

## 9. `EvalRuns/`：每次测试的真实现场

每次 `prepare` 会生成一个新的 Run：

```text
D:\work\AI-Native\EvalRuns\<run-id>\
└── workspace/
    ├── game-engine/       # 本次项目快照，也是被测 Agent 工作目录
    ├── ai-native-dsh/     # DSH 快照
    ├── output/            # Agent 候选产物
    ├── scratch/           # 临时文件
    ├── evidence/          # Check 结果和最终 Verdict
    ├── trace/             # 被测 Agent 和评测 Agent 日志
    └── agent-config/      # 本次运行的 MCP 配置
```

关键原则：

```text
每个 Task Run 一个独立 Workspace
不同 Run 不共享可写 Workspace
评测阶段只读复用该 Workspace
运行结果不要手动编辑
```

例如：

```text
workspace/output/agent-result.json
    Agent 真实产物

workspace/evidence/checks/validate-result-contract.json
    一个 Check 的结果

workspace/evidence/evaluation.json
    所有 Check 的聚合结果

workspace/trace/evaluators/<role>/<attempt-id>/
    Outcome/Quality Agent 的独立日志
```

## 10. 如何定义一个新 Task

### 第一步：确定 Task ID

例如：

```text
structured-report-contract
```

Task ID 必须稳定、唯一，后面会出现在：

```text
run-manifest.json
EvalRuns/<run-id>
summary 表
```

### 第二步：在 `config/eval.yaml` 添加 Task

最小结构：

```yaml
tasks:
  my-task:
    agent: codex
    mcp_profile: none
    prompt: >-
      在 /workspace/output 生成任务结果，并完成自检。
    test_plan:
      version: 1
      checks:
        ...
```

`agent` 从顶层 `agents` 中选择：

```yaml
agents:
  codex:
    image: ai-native-codex-agent:local
  codex-dcc:
    image: ai-native-codex-agent:all-mcp
```

`mcp_profile` 从顶层 `mcp_profiles` 中选择：

```yaml
mcp_profile: none
```

或：

```yaml
mcp_profile: blender-ue5-host
```

### 第三步：规定 Workspace 输出位置

推荐让被测 Agent 把候选产物放在：

```text
/workspace/output/
```

例如：

```text
/workspace/output/result.json
/workspace/output/model.blend
/workspace/output/texture.png
```

这是容器内路径；宿主上对应：

```text
D:\work\AI-Native\EvalRuns\<run-id>\workspace\output\
```

复杂 Task 不要把产物直接放到 `evidence/`。`evidence/` 应由评测系统写入 Check 结果。

### 第四步：写 TestPlan

一个 Check 的通用格式：

```yaml
- id: validate-result
  phase: outcome
  evaluator: script.json_contract.v1
  input:
    artifact: locate-result.selected_artifact
  config:
    required_fields:
      - task_id
      - status
  required: true
  weight: 1.0
  depends_on:
    - locate-result
  on_error: fail
```

字段说明：

```text
id
    当前 Task 内唯一的检查名称

phase
    outcome / quality / process

evaluator
    使用哪个通用 Evaluator

input
    Evaluator 消费的 Workspace/Evidence 或前置 Check 输出

config
    这个 Task 传给 Evaluator 的具体要求

required
    是否属于硬性检查

weight
    质量/过程分数的权重

depends_on
    必须先完成的 Check

on_error
    fail / review / skip
```

### 第五步：选择 Evaluator

优先从现有列表选择：

```yaml
agent.artifact_locator.v1
script.file_exists.v1
script.text_equals.v1
script.json_contract.v1
script.blender_scene.v1
script.ue5_state.v1
agent.quality_judge.v1
trace.process_analyzer.v1
```

例如：

```yaml
checks:
  - id: locate-result
    phase: outcome
    evaluator: agent.artifact_locator.v1
    input:
      roots:
        - /workspace/output
    config:
      prompt: prompts/outcome/artifact-locator-v1.md
      artifact_kind: json_document
      expected_name: result.json
    required: true

  - id: validate-result
    phase: outcome
    evaluator: script.json_contract.v1
    input:
      artifact: locate-result.selected_artifact
    config:
      required_fields:
        - task_id
        - status
    required: true
    depends_on:
      - locate-result
```

这里表达的是：

```text
先让独立 Outcome Locator Agent 找 result.json
再让脚本读取 Locator 选中的真实文件
```

### 第六步：如果需要 Quality Judge

新增 Rubric：

```text
config/rubrics/my-task.yaml
```

新增或复用 Quality Prompt：

```text
prompts/quality/my-task-v1.md
```

在 TestPlan 中添加：

```yaml
- id: result-quality
  phase: quality
  evaluator: agent.quality_judge.v1
  input:
    artifact: validate-result.path
  config:
    prompt: prompts/quality/my-task-v1.md
    rubric: config/rubrics/my-task.yaml
  required: false
  weight: 1.0
  on_error: review
  depends_on:
    - validate-result
```

Quality Judge 会在独立 Docker 容器中运行，只读当前 Run 的 Workspace/Evidence。

### 第七步：先只检查计划，不跑 Agent

```powershell
cd D:\work\AI-Native\AI-Native-Evals
uv run ai-native-evals run plan my-task
```

这个命令会检查：

```text
Task 是否存在
TestPlan 是否能解析
Check ID 是否重复
Evaluator 配置是否能读取
依赖关系是否有效
执行顺序是什么
```

### 第八步：执行完整评测

如果需要宿主 Blender/UE5，先启动：

```powershell
pwsh -NoProfile -File .\tools\start-eval-hosts.ps1
```

执行一条完整 Task：

```powershell
uv run ai-native-evals run execute my-task
```

`execute` 会自动执行：

```text
prepare
→ 启动被测 Agent Docker
→ wait
→ 执行 TestPlan
→ 写 evaluation.json / verdict.json
```

如果需要分阶段控制：

```powershell
uv run ai-native-evals run prepare my-task --agent codex
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run logs <run-id>
uv run ai-native-evals run wait <run-id>
uv run ai-native-evals run evaluate <run-id>
```

### 第九步：查看结果

```powershell
uv run ai-native-evals run status <run-id>
uv run ai-native-evals run digest <run-id>
uv run ai-native-evals run summary
```

重点看：

```text
workspace/output/
    Agent 是否留下了真实产物

workspace/evidence/checks/
    每个 Check 为什么通过或失败

workspace/evidence/evaluation.json
    最终 decision、各阶段分数

workspace/trace/
    被测 Agent 的完整过程

workspace/trace/evaluators/
    Outcome/Quality Agent 的过程
```

### 第十步：提交哪些文件

应该提交：

```text
config/eval.yaml
config/rubrics/*.yaml
prompts/**/*.md
必要的 Evaluator 代码
测试代码
相关文档
```

不要提交：

```text
config/.env.local
EvalRuns/运行现场
cache/docker/*.tar
cache/python/*/wheels/*.whl
cache/codex/*.tgz
API key
```

## 11. 一个完整 Task 的最小心智模型

```text
我想测试什么？
    → Task Prompt

我要检查哪些结果？
    → TestPlan.checks

每项结果怎么检查？
    → evaluator + input + config

结果质量怎么判断？
    → Quality Prompt + Rubric

过程怎么看？
    → trace.process_analyzer.v1

运行后去哪里看？
    → EvalRuns/<run-id>/workspace/
```

## 12. 当前推荐的编写顺序

```text
1. 先写 Task Prompt
2. 规定 /workspace/output 的产物
3. 用现有 Evaluator 组合 TestPlan
4. 用 run plan 检查计划
5. 执行 run execute
6. 检查 evidence/checks/*.json
7. 检查 trace 和 digest
8. 只有表达不了时才新增 Evaluator 代码
```

核心原则是：

> **Task 定义目标，TestPlan 定义检查组合，Check 选择 Evaluator，Rubric 定义质量标准，Workspace 保存事实现场。**
