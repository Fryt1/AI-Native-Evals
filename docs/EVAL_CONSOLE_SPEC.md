# AI-Native Eval Console 产品与交互规格

- 状态：Proposed
- 日期：2026-09-10
- 适用仓库：`AI-Native-Evals`
- 产品名称：AI-Native Eval Console
- 目标版本：Console v1

## 1. 产品定义

AI-Native Eval Console 是 AI-Native-Evals 的唯一正式可视化前端。它用于回答四个问题：

1. 这次 Agent 任务最终有没有完成？
2. Outcome、Quality、Process 为什么得到这个分数？
3. Agent 在执行过程中做了什么、在哪里出错、是否绕路？
4. 不同 Agent、模型或 Profile 在相同条件下有什么可解释的差异？

它不是通用 BI Dashboard，也不是聊天界面，更不是文件管理器。它是一套面向 Agent 工程师、TA/TD 和评测设计者的“评测飞行记录仪”。

### 1.1 一句话体验目标

> 用户打开一个 Run，在 10 秒内知道结果；在 60 秒内定位失败环节；在 5 分钟内获得足够证据决定应该修改 Agent、Task、Evaluator 还是环境。

## 2. 背景与问题

当前系统已经能生成：

- Run Manifest
- Agent 统一 Trace
- Outcome / Quality / Process 评分
- Check 结果与证据
- Evaluator 子 Agent Trace
- Workspace 输出和中间产物
- Codex / DSH Comparison

但是 Inspect Viewer 的核心模型仍然是 EvalLog、Sample、Message、Score，无法自然表达以下 AI-Native 领域对象：

- Run 生命周期与 Docker Sandbox
- Agent / Model / MCP / Preset 的解析来源
- Outcome Locator → Script Validator 的证据链
- Quality Judge 的独立 Agent 运行
- Process Scorer 与具体 Trace 事件的关系
- Workspace Artifact、Blender、UE5 证据
- 多 Agent 公平对比的 invariant

因此 Console 必须建立在 AI-Native-Evals 自己的稳定数据契约上，而不是继续扩展或 fork Inspect Viewer。

## 3. 范围

### 3.1 Console v1 必须提供

- Run 列表、筛选、搜索和状态识别
- 单次 Run 总览
- Agent Trace：Turn、Step、消息、工具、命令、文件变化和错误
- Evaluation：Outcome、Quality、Process 及其 Check 证据链
- Evaluator 子运行查看
- Artifact 与 Evidence 预览
- Comparison：相同 Task 下的 Agent / Model 对比
- Task、Agent、Model、MCP、Sandbox、Preset 只读 Registry
- 从已有配置选择 Task、Agent、Model、MCP、Sandbox、Preset
- Run Plan Preview：显示最终解析配置后再执行
- 通过 Console API 启动 Docker 评测，CLI 与 UI 复用同一个 RunApplication
- 运行中 Run 的轮询更新；SSE 作为 v1.1

### 3.2 Console v1 不提供

- 在浏览器中编辑 `eval.yaml` 或 Profile YAML
- 在浏览器中编辑 Task Prompt / Rubric
- 任意宿主机文件浏览
- 浏览器直接控制 Docker、Blender 或 UE5（只能通过受控 Run API 间接发起评测）
- 远程多用户、账号、权限系统
- Phoenix、Langfuse 或云服务依赖
- 通用 BI 报表设计器
- 浏览器解析 `.blend`、`.uasset` 等原生二进制格式
- 替代 Inspect AI 的 Task / Solver / Scorer 能力

## 4. 用户与核心任务

### 4.1 Agent 工程师

需要快速判断 Agent 的 Loop、上下文、工具使用和错误恢复能力。

核心任务：

- 查找失败 Run
- 查看 Agent 的 Turn / Step
- 检查 Tool Call 与 Tool Result
- 判断是否反复调用同一工具
- 对比 Codex 与 DSH 的行为差异

### 4.2 TA / TD / DCC 管线工程师

需要确认 Agent 是否真的在 Blender / UE5 中生成了正确世界状态。

核心任务：

- 查看结构化 DCC 验证证据
- 查看截图、对象摘要、Transform、材质和组件
- 区分“Agent 声称完成”和“宿主世界状态验证通过”
- 定位 MCP、DCC 或 Agent 责任边界

### 4.3 评测设计者

需要确认 Task、Check、Evaluator 和 Rubric 是否科学。

核心任务：

- 查看 Check DAG
- 查看 Evaluator 输入和输出
- 查看 LLM Judge 的 Prompt、Rubric、模型和解释
- 发现 Evaluator 错误、证据缺失或评分不稳定
- 比较同一 Task 的多次 Run

## 5. 产品设计原则

### 5.1 Evidence first

任何分数和结论都必须能点击到证据。页面不允许只显示一个 0.72 而不解释来源。

```text
Verdict → Phase Score → Check → Evaluator → Evidence → Trace Event / Artifact
```

### 5.2 Failure first

默认优先展示：

1. Running
2. Failed
3. Review
4. Completed + Pass

错误页面必须说明：发生了什么、影响什么、下一步看哪里。

### 5.3 Configuration is evidence

Agent、模型、推理强度、MCP、Sandbox、Docker Image、Git Snapshot 都属于评测证据，不是普通设置。

### 5.4 Provider-neutral UI

Provider 和 Model 是两个独立的一等配置字段。前端可以展示和选择它们，但不能出现 Codex JSONL、DSH ACP 或某个 Provider 私有协议的页面分支。Model Binding 只负责把 Provider、Model、Protocol 和推理参数解析成一次 Run 的固定配置。

### 5.5 Dense, not crowded

这是桌面工程工具。允许高信息密度，但必须通过对齐、层级、留白和逐层展开避免拥挤。

### 5.6 Progressive disclosure

默认显示结论和摘要；原始 Payload、完整日志和低层字段在 Inspector 中按需展开。

### 5.7 One action, one meaning

按钮使用明确动词：

- 查看 Trace
- 打开证据
- 复制 Run ID
- 与此 Run 对比
- 重新索引

禁止使用含糊的“更多”“处理”“提交”。

## 6. 系统边界

```text
Evaluation Core
    │ 写入稳定文件契约
    ▼
EvalRuns ──► Console Read Model ──► Console API ──► React Console
    │
    └──────► 可选 Inspect / OpenInference Export
```

### 6.1 依赖规则

- `ai_native_evals` 的评测模块不得引用 `ai_native_evals_console`；顶层 CLI 仅允许作为可选命令分发入口调用 Console。
- Console 不得导入 Agent Adapter 实现
- Web 不得直接读取 EvalRuns
- SQLite Catalog 不是事实来源，必须可删除重建
- API 不得向浏览器暴露绝对宿主路径
- Console 不得读取 `config/.env.local` 的秘密值

### 6.2 仓库布局

```text
AI-Native-Evals/
├── src/ai_native_evals/             # 现有评测核心
├── src/ai_native_evals_console/     # Read Model、Catalog、API、安全层
├── apps/eval-console/               # React + TypeScript
├── schemas/                         # EvalRuns 持久化契约
├── tests/console/                   # Console 后端测试
├── tests/fixtures/console/          # 固定 Run Fixture
└── docs/EVAL_CONSOLE_SPEC.md
```

## 7. 数据契约

### 7.1 Run 生命周期

```text
prepared → starting → running → evaluating → completed
                         ├───────────────→ failed
                         └───────────────→ stopped
```

执行状态和评测结论必须分开：

```text
lifecycle_status: prepared | starting | running | evaluating | completed | failed | stopped
decision: pass | fail | review | not_evaluable | null
```

`completed + fail` 表示执行完成但验收失败；`failed + not_evaluable` 表示执行本身未完成，无法形成可靠验收结论。

### 7.2 Run Summary DTO

Run 列表只返回轻量摘要：

```text
run_id
task_id
agent_id
model_id
lifecycle_status
decision
outcome_score
quality_score
process_score
started_at
finished_at
duration_ms
failed_check_count
error_count
artifact_count
comparison_ids
is_legacy
```

### 7.3 Normalized Agent Event v1

每个事件至少包含：

```text
schema_version
event_id
run_id
seq
occurred_at
type
actor.kind
actor.id
turn_id
step_id
parent_event_id
call_id
status
duration_ms
summary
payload
artifact_refs
source.adapter
source.source_type
```

事件类型：

```text
run_started
run_completed
turn_started
turn_completed
model_request
model_response
agent_message
reasoning
tool_call
tool_result
command_started
command_completed
file_changed
approval_requested
error
provider_event
```

Actor 类型：

```text
subject_agent
outcome_locator
quality_judge
process_analyzer
framework
sandbox
```

Tool Call 与 Tool Result 通过 `call_id` 关联。Started 与 Completed 事件通过 `call_id` 或 `parent_event_id` 关联。旧数据无法恢复的字段返回 `null`，不得伪造。

### 7.4 Evaluation Check

```text
check_id
phase: outcome | quality | process
evaluator
annotator_kind: code | llm | human | hybrid
status
passed
score
weight
required
depends_on
explanation
evidence_refs
evaluator_run_id
started_at
finished_at
```

### 7.5 Artifact Reference

新 Run 必须生成 `workspace/artifacts/manifest.json`，Console 不递归扫描完整项目快照。

```text
artifact_id
role: subject_output | evaluation_evidence | preview | diff | diagnostic
relative_path
kind
mime_type
size
sha256
created_by
previewable
```

默认忽略：

```text
.git
.venv
node_modules
Intermediate
DerivedDataCache
Saved/webcache
__pycache__
```

### 7.6 Comparison

Comparison 必须记录：

```text
comparison_id
created_at
invariant_hash
invariants
dimensions
run_ids
```

公平比较的默认 invariants：

```text
task_id
prompt_hash
test_plan_hash
resource_snapshot_hash
sandbox_profile
mcp_profile
evaluator_profile
```

如果 invariant 不一致，UI 必须显示“配置不一致，仅供参考”。

## 8. Console 后端规格

### 8.1 Catalog

位置：

```text
<EvalRuns>/.console/catalog.sqlite
```

Catalog 保存摘要和文件偏移，不保存大型原始 Payload 或 Artifact 内容。

主要表：

```text
runs
checks
comparisons
comparison_runs
events_index
artifacts
evaluator_runs
index_state
```

### 8.2 增量索引

索引器必须：

- 只检查新增或改变的 Manifest / Evaluation / Trace 文件
- 使用 mtime、size 和内容 hash 判断更新
- 容忍运行中的 JSONL 最后一行不完整
- 容忍缺失 Manifest、损坏 JSON、缺失 Evidence
- 不递归扫描项目快照
- 可通过命令完全重建

### 8.3 Reader 版本

```text
LegacyRunReader：读取没有 schema_version 的现有 Run
RunV1Reader：读取新契约
```

API 输出统一 DTO，前端不感知磁盘版本。

### 8.4 API

```text
GET /api/v1/health
GET /api/v1/runs
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/checks
GET /api/v1/runs/{run_id}/events
GET /api/v1/runs/{run_id}/evaluator-runs
GET /api/v1/runs/{run_id}/artifacts
GET /api/v1/runs/{run_id}/artifacts/{artifact_id}
GET /api/v1/comparisons
GET /api/v1/comparisons/{comparison_id}
GET /api/v1/registry
```

Run 列表支持：

```text
task_id
agent_id
model_id
lifecycle_status
decision
has_errors
created_after
created_before
query
limit
cursor
```

Event 查询支持：

```text
after_seq
limit
type
actor_kind
turn_id
step_id
status
query
```

### 8.5 Artifact 安全

- 浏览器只提交 `run_id + artifact_id`
- 服务端从 Artifact Manifest 解析真实文件
- 文件解析后必须位于该 Run Workspace 内
- 禁止 `..`、绝对路径和符号链接逃逸
- 默认文本预览上限 1MB
- 大日志使用 Range / 分块读取
- 二进制文件只提供元数据或明确允许的下载
- `.env`、密钥、Agent 私有 Home 永不进入 Artifact Manifest

### 8.6 实时更新

Console v1 使用短轮询更新运行状态。v1.1 提供 SSE：

```text
run_updated
event_appended
check_completed
artifact_created
run_completed
run_failed
```

### 8.7 Run Plan 与执行 Job

UI 不直接拼接 shell 命令。执行分成两个明确阶段：

```text
POST /api/v1/run-plans
    ↓
Resolved Plan（Task / Agent / Model / MCP / Sandbox / Evaluator）
    ↓ 用户确认
POST /api/v1/run-plans/{plan_id}/execute
    ↓
后台 Job → prepare_run → start_docker_run → wait_docker_run → evaluate_run
```

Run Plan 只保存本次请求引用的 Profile ID 和解析后的 `RunSpec`；Profile 内容由服务端读取。执行返回 `job_id` 和 `run_id`，前端轮询 Job 状态。

配置表单只向用户要四件事：**Task、Provider、Model、Reasoning**。Agent、MCP、Sandbox 收进可折叠的「高级选项」，留空即由 Task 与项目默认值决定。

```text
Task           codex-file-smoke
Provider       sub2api          ← 上游
Model          gpt-5.6-luna     ← 该上游实际提供的模型
Reasoning      high             ← 该模型 × 该 Agent 都接受的等级
```

两条硬规则：

- **Model 列表来自 Provider 自己**（`GET /v1/models`），不是仓库里手写的清单。手写清单会过期——本仓库就曾长期保留一个上游已停止提供的模型绑定，导致 Run 在请求时才 404。
- **Provider 与 Binding 不是一回事。** Provider 是上游（base_url + 凭据 + 协议 + 模型集合），Binding 是历史遗留的技术映射。表单不再展示 Binding；当所选 provider/model 恰好命中一个既有 Binding 时才复用它，否则用 `<provider>:<model>` 作为技术标识。

Reasoning 的可选值是 **Provider 声明的等级 ∩ Agent 能发出的等级** 的交集，两侧都会拒绝未知取值。Provider 拒绝一个未知等级时会回列全部合法值，因此该交集在预览阶段即可确定；非法组合在启动 Docker 之前返回 422。

执行状态：

```text
queued → preparing → prepared → running → evaluating → completed
                                      └──────────────→ failed / error
```

`POST /api/v1/runs` 只接受已有 `plan_id`，不能绕过 Preview 直接传入任意命令或任意宿主路径。

## 9. 信息架构

Console 不设置独立的“图表 Dashboard”。`Runs` 页面本身就是工作入口，避免多一层无用首页。

```text
Runs
├── Run Overview
├── Trace
├── Evaluations
├── Artifacts
└── Configuration

Comparisons
└── Comparison Detail

Registry
├── Tasks
├── Agents
├── Models
├── MCP
├── Sandboxes
└── Presets
```

全局导航：

```text
Runs
Compare
Registry
```

全局快捷键：

```text
Ctrl+K：搜索 Run / Task / Agent / Comparison
/：聚焦当前页面搜索
Esc：关闭 Inspector / Dialog
J / K：在 Trace 事件间移动
Enter：打开当前事件
```

## 10. 视觉方向

### 10.1 设计概念：Evidence Instrument

视觉语言来自三类真实工作对象：

- 飞行记录仪：状态、时间、事件、故障
- DCC Viewport：深色工作区、高对比选择态、长时间使用
- 工程证据链：节点、连线、因果关系、检查结果

它不是“赛博朋克大屏”，也不是“卡片堆叠后台”。整体应该安静、精确、可信。

### 10.2 标志性元素：Evidence Spine

Run 页面上存在一条持续可见的证据主线：

```text
Prompt → Agent Actions → Artifact → Checks → Verdict
```

用户点击 Outcome、Quality、Process、Check 或 Artifact 时，Evidence Spine 高亮当前节点和上下游关系。它既是导航，也是解释结构，是本产品唯一明显的视觉记忆点。

Live Run 时，当前节点允许使用一次缓慢的脉冲动画；普通页面不使用持续动画。

### 10.3 色彩

基础色：

```text
Workshop Ink       #0E131B  页面背景
Instrument Panel   #151C27  面板背景
Raised Surface     #1C2634  悬浮和选中背景
Hairline           #2B3747  边界与分隔
Primary Text       #ECF1F7  主文字
Secondary Text     #9BA8B8  次级文字
```

语义色：

```text
Signal Blue        #62AFFF  交互、选中、链接
Pass Mint          #45C69B  通过、成功
Review Amber       #F2B35D  Review、等待、不确定
Failure Coral      #FF7477  失败、错误
Judge Violet       #A997FF  LLM Judge / Quality
Process Cyan       #5CC8D7  Process / Trace
```

规则：

- 颜色只表达状态和关系，不做无意义装饰
- Pass / Fail 同时使用图标和文字，不能只靠颜色
- 大面积背景不使用高饱和色
- 不使用彩虹渐变和玻璃拟态

### 10.4 字体

```text
UI / 正文：IBM Plex Sans
标识 / 小标题：IBM Plex Sans Condensed
代码 / ID / 数值：IBM Plex Mono
```

字体文件随应用打包，不依赖外网。

### 10.5 尺寸与密度

```text
基础间距：4px
常用间距：8 / 12 / 16 / 24 / 32
面板圆角：6px
输入框圆角：5px
标签圆角：999px，仅用于状态标签
边界：1px
默认表格行高：44px
紧凑表格行高：36px
Inspector 宽度：400px
全局侧栏展开：208px
全局侧栏收起：56px
```

不使用大量 16px 圆角大卡片。工程界面应以分区、表格、轨道和 Inspector 为主要结构。

### 10.6 阴影与层级

- 常规面板依赖边界和色差，不使用阴影
- Inspector、Popover、Dialog 才使用阴影
- 选中态使用左侧 2px Signal Blue，不使用整块强亮背景

### 10.7 动效

允许：

- Inspector 160ms 滑入
- 行展开 140ms
- Live 节点 1.8s 低强度脉冲
- 数值更新时 200ms 背景闪烁

禁止：

- 页面加载逐卡片飞入
- 无限流动背景
- 大面积呼吸光
- 分数滚动计数动画

`prefers-reduced-motion` 下关闭非必要动效。

## 11. 应用壳层

桌面布局：

```text
┌──────────┬──────────────────────────────────────────────────┐
│ Logo     │ Context Header                     Ctrl+K        │
│          ├──────────────────────────────────────────────────┤
│ Runs     │                                                  │
│ Compare  │ Main Content                                     │
│ Registry │                                                  │
│          │                                                  │
│          │                                                  │
│ Index    │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

侧栏底部显示：

```text
Index: Ready / Updating / Error
Runs Root 状态
Console 版本
```

Context Header 根据页面显示：

```text
Runs / 当前筛选
Run ID / Task / Agent
Comparison ID / Task
Registry / 当前分类
```

## 12. Runs 页面

### 12.1 页面任务

让用户快速找到“需要处理的 Run”。

### 12.2 页面结构

```text
┌──────────────────────────────────────────────────────────────┐
│ Runs                                      Search / Filters    │
├──────────────────────────────────────────────────────────────┤
│ Running 2   Failed 4   Review 1   Passed 20                  │
├──────────────────────────────────────────────────────────────┤
│ Task            Agent       Model        Result       Scores │
│ structured...   codex       deepseek     PASS         1/1/1 │
│ roundtrip...    dsh         deepseek     FAIL         0/.8/.6│
│ ...                                                          │
└──────────────────────────────────────────────────────────────┘
```

顶部状态数字是筛选器，不是装饰 KPI。点击 `Failed 4` 直接过滤列表。

### 12.3 Run 行

一行显示：

- 生命周期图标
- Task ID
- Agent
- Model
- Outcome / Quality / Process 三段迷你条
- Decision
- Duration
- Failed Check 数
- 开始时间

悬停显示快捷动作：

- 打开
- 复制 Run ID
- 与此 Run 对比

### 12.4 筛选

默认显示可关闭的筛选 Chips：

```text
Status: Failed
Agent: DSH
Task: roundtrip-blender-ue5-v3
```

“清除筛选”必须始终可见。

### 12.5 状态

空状态：

```text
还没有 Run
先通过 CLI 执行一个 Task，Console 会自动索引运行结果。
```

索引失败：

```text
Run 索引未完成
2 个 Run 的 Manifest 无法读取。查看诊断 / 重新索引
```

## 13. Run Overview 页面

### 13.1 页面任务

在不阅读完整 Trace 的前提下解释本次运行结果。

### 13.2 页面结构

```text
┌──────────────────────────────────────────────────────────────┐
│ structured-report-contract                                  │
│ cf275d82f0  CODEX  deepseek-v4-flash  COMPLETED / PASS      │
├──────────────────────────────────────────────────────────────┤
│ Prepared ── Agent ── Evaluating ── Verdict                   │
├─────────────────────┬────────────────────────────────────────┤
│ OUTCOME   1.00      │ Evidence Spine                         │
│ QUALITY   1.00      │ Prompt → Actions → Artifact → Checks   │
│ PROCESS   1.00      │                             → PASS      │
├─────────────────────┼────────────────────────────────────────┤
│ Checks              │ What happened                          │
│ 4 passed            │ 1 command · 0 failed · 21.3 s         │
│ 0 failed            │ Last message: Created agent-result... │
├─────────────────────┴────────────────────────────────────────┤
│ Outputs / Problems / Configuration summary                  │
└──────────────────────────────────────────────────────────────┘
```

### 13.3 Score 模块

每个 Score 显示：

- 名称
- 分数或 `未评测`
- 状态
- 贡献 Check 数
- 最重要的解释
- `查看证据`

点击后打开对应 Evaluation，而不是弹出只含数字的 Tooltip。

### 13.4 Problems 模块

只显示需要注意的内容：

```text
1 Agent warning
0 failed actions
0 failed required checks
```

模型 metadata fallback 等非致命错误标为 Warning，不得和任务失败混为一谈。

## 14. Trace 页面

### 14.1 页面任务

阅读 Agent 的真实执行过程，并快速定位错误、重复、绕路和无效动作。

### 14.2 布局

```text
┌───────────────────────────────────────────┬──────────────────┐
│ Filters / Search / Turn selector          │ Event Inspector  │
├───────────────────────────────────────────┤                  │
│ Turn 1                                    │ Type             │
│  ├ 00:00 Agent message                    │ Status           │
│  ├ Step 1                                 │ Duration         │
│  │ ├ Reasoning summary                    │ Arguments        │
│  │ ├ Command started                      │ Result           │
│  │ └ Command completed  0                 │ Related files    │
│  └ 00:21 Agent completed                  │ Raw payload      │
└───────────────────────────────────────────┴──────────────────┘
```

### 14.3 Event Rail

每种事件拥有固定形状和颜色：

```text
Agent message       圆形 / Signal Blue
Reasoning summary   菱形 / Judge Violet
Tool call           向右箭头 / Review Amber
Tool result         向左箭头 / Pass Mint 或 Failure Coral
Command             方形 / Process Cyan
File changed        文件形 / Signal Blue
Error               八边形 / Failure Coral
Lifecycle           小圆点 / Secondary Text
```

同时必须显示文本标签，不能只靠图形。

### 14.4 事件配对

Tool Call 和 Tool Result 在轨道上通过细线连接。点击任意一端，另一端同步高亮。

Started / Completed 事件默认合并为一个动作行：

```text
✓ bash command                   1.2s
```

用户可以展开查看原始 Started / Completed 事件。

### 14.5 Trace 过滤

```text
Actor
Turn
Event Type
Status
Tool / Command
Only errors
Only file changes
```

搜索命中内容时在 Event Inspector 中高亮，但不修改原始文本。

### 14.6 Reasoning

只显示 Agent 协议明确输出的 reasoning summary。标签必须写“Reasoning summary”，不能暗示这是完整隐藏思维链。

### 14.7 性能

- 使用虚拟列表
- 首次最多加载 100 个事件
- 向前/向后游标分页
- Payload 在打开 Inspector 后再获取或展开
- 单个超大 Tool Result 默认截断并提供“加载更多”

## 15. Evaluations 页面

### 15.1 页面任务

让用户理解分数是怎样从 Check 和 Evidence 得出的。

### 15.2 布局

```text
┌──────────────────────────────┬───────────────────────────────┐
│ Check Graph                  │ Check Detail                  │
│                              │                               │
│ locate-result                │ report-quality                │
│      ↓                       │ Quality · LLM Judge           │
│ validate-contract ──→ quality│ Score 1.00                    │
│                              │ Rubric / Prompt / Judge Model │
│ process-observation          │ Explanation                   │
│                              │ Evidence                      │
└──────────────────────────────┴───────────────────────────────┘
```

### 15.3 Check 节点

显示：

- Check ID
- Phase
- Evaluator 类型
- CODE / LLM / HUMAN / HYBRID
- Passed / Failed / Review / Error / Skipped
- Score
- Required

### 15.4 Check Detail

分区顺序固定：

1. Result
2. Why
3. Inputs
4. Evaluator configuration
5. Evidence
6. Evaluator run
7. Raw result

如果 Check 使用 Evaluator Agent，显示：

```text
查看 Judge Trace
```

进入嵌套 Trace 后，页面顶部明确显示：

```text
Evaluator Run · quality-judge-report-quality
Parent Run · structured-report-contract-cf275d82f0
```

## 16. Artifacts 页面

### 16.1 页面任务

查看 Agent 产物和评分证据，而不是浏览整个磁盘。

### 16.2 分类

```text
Outputs
Evidence
Previews
Diffs
Diagnostics
```

### 16.3 布局

```text
┌──────────────────────────────┬───────────────────────────────┐
│ Artifact List                │ Preview                       │
│                              │                               │
│ agent-result.json            │ {                             │
│ quality-result.json          │   "status": "completed"      │
│ scene-summary.json           │ }                             │
│ blender-preview.png          │                               │
└──────────────────────────────┴───────────────────────────────┘
```

### 16.4 预览器

```text
JSON        折叠 Tree + Raw 切换
Text/Log    行号、搜索、复制、截断提示
Image       缩放、原始尺寸、下载
Diff        Unified / Side-by-side
Binary      文件信息、Hash、下载；不尝试解析
```

Blender / UE5 结果优先展示评测时生成的截图和结构化 Scene Evidence。

## 17. Comparison 页面

### 17.1 页面任务

解释两个或多个 Run 的差异，而不是只宣布谁分数高。

### 17.2 顶部公平性门禁

```text
FAIR COMPARISON
Task、Prompt、TestPlan、Resource、Sandbox、Evaluator 一致
Variable: Agent
```

不一致时：

```text
CONFIGURATION MISMATCH
Model Profile 和 MCP Profile 不同，本比较仅供参考。
```

### 17.3 Summary Matrix

```text
┌──────────────────────┬───────────────┬───────────────┬─────────┐
│ Metric               │ Codex         │ DSH           │ Delta   │
├──────────────────────┼───────────────┼───────────────┼─────────┤
│ Outcome              │ 1.00          │ 1.00          │ 0       │
│ Quality              │ 1.00          │ 1.00          │ 0       │
│ Process              │ 1.00          │ 0.714         │ +0.286  │
│ Duration             │ 21.3s         │ 35.2s         │ -13.9s  │
│ Failed actions       │ 0             │ 2             │ -2      │
└──────────────────────┴───────────────┴───────────────┴─────────┘
```

### 17.4 Check 对比

按 Check ID 对齐：

```text
locate-result
validate-result-contract
report-quality
process-observation
```

缺失 Check 必须显示 `Missing`，不能按 0 分处理。

### 17.5 Trace 对比

v1 提供摘要对比：

```text
Turns
Steps
Tool calls
Failed actions
Repeated tools
File changes
Warnings
Duration
```

逐事件同步 Trace Diff 属于 v1.2，不作为第一版门禁。

## 18. Registry 页面

Registry 只读展示当前定义：

```text
Tasks
Agents
Models
MCP
Sandboxes
Presets
```

每个 Profile 显示：

- ID
- 类型
- 来源文件的相对路径
- 关键能力
- 引用它的 Preset
- 最近被哪些 Run 使用

秘密字段显示：

```text
Configured
Missing
```

永远不显示真实值。

## 19. 响应式与桌面策略

主要目标是 1280px 以上桌面。Console 仍需在小窗口可用：

```text
≥ 1440px   Timeline + Inspector 并排
1024–1439  Inspector 可折叠
768–1023   Inspector 变为右侧 Drawer
< 768      支持查看摘要；复杂 Compare 提示使用更宽窗口
```

不为了手机强行破坏桌面信息密度。

## 20. 可访问性

- 所有交互可键盘操作
- 明确的 `:focus-visible`
- 状态不只依赖颜色
- 文本和背景达到 WCAG AA 对比度
- Tooltip 不能承载唯一信息
- Event Rail 提供可读文本
- 支持 `prefers-reduced-motion`
- 图表必须有对应表格或可读摘要

## 21. 文案规范

页面默认使用中文，保留领域对象英文标识：

```text
结果 Outcome
质量 Quality
过程 Process
Run
Task
Agent
Model
Check
Trace
Artifact
```

错误文案结构：

```text
发生了什么
影响什么
下一步可以做什么
```

示例：

```text
无法读取 Evaluation Report
本次 Run 仍可查看 Trace，但不能显示 Outcome、Quality 和 Process。
检查 workspace/evidence/evaluation.json，或重新执行评测。
```

禁止：

```text
出错了
Oops
Something went wrong
未知错误，请重试
```

## 22. 技术选型

### 22.1 Web

```text
React
TypeScript
Vite
React Router
TanStack Query
TanStack Table
虚拟列表
Radix UI Primitives
CSS Variables + CSS Modules
ECharts，仅用于确实需要的数据图
```

不使用复制式组件库作为视觉基础。Radix 只提供无样式交互基础，视觉由 Console Token System 控制。

服务器状态使用 TanStack Query；局部 UI 状态使用 React。v1 不引入 Redux。

### 22.2 Backend

```text
FastAPI
Pydantic API DTO
SQLite Catalog
现有 EvalRuns 文件
SSE，v1.1
```

FastAPI 的 OpenAPI 文档作为 Web API TypeScript Client 的生成来源，避免手写两套 API 类型。

### 22.3 发布

开发：

```text
Vite 5173
FastAPI 8787
```

生产：

```text
React build → Python Package Static Assets → FastAPI 同源托管
```

最终命令：

```powershell
uv run ai-native-evals console
```

## 23. 测试规格

### 23.1 后端行为测试

覆盖：

- Legacy Run
- Completed / Failed / Prepared / Stopped Run
- 缺失 Manifest
- 损坏 Evaluation
- 不完整 JSONL
- Artifact 路径穿越
- 超大文件预览
- Catalog 增量更新和重建
- Comparison invariant mismatch

测试外部行为，不依赖 SQLite 的具体 SQL 实现。

### 23.2 前端组件测试

覆盖：

- Run 状态和 Decision 正确区分
- `null` Score 显示“未评测”而不是 0
- Error / Warning / Failed Check 正确分类
- Tool Call 和 Result 配对
- Check 点击打开 Evidence
- Artifact 类型选择正确预览器
- Registry 不泄露秘密

### 23.3 Playwright E2E

固定 Fixture：

```text
completed-codex-run
completed-dsh-run
failed-run
running-run
legacy-run
evaluator-run
fair-comparison
mismatched-comparison
```

关键流程：

1. 从 Runs 过滤失败 Run
2. 打开 Run 并查看失败 Check
3. 从 Check 跳到 Evidence
4. 从 Evidence 跳到 Evaluator Trace
5. 打开 Artifact 预览
6. 打开 Comparison 并识别公平性

UI 测试不得调用真实模型、Docker、Blender 或 UE5。

## 24. 性能门禁

使用生成 Fixture 验证：

```text
10,000 Runs
1,000 Comparisons
单 Run 100,000 Events
单日志 100MB
```

目标：

- 已建立索引时 Runs 首屏 API P95 < 300ms
- Run Detail API P95 < 300ms
- 100 条 Event 页面 P95 < 250ms
- 浏览器首次可交互 < 2s，本地开发机器
- Trace DOM 节点保持有界，不随总 Event 数线性增长
- 启动 Console 不递归扫描 Workspace Snapshot

## 25. 安全门禁

- API 只能访问配置解析出的 `runs_root`
- Artifact 必须来自 Manifest
- 拒绝绝对路径、`..` 和越界符号链接
- API 响应不包含 API Key、Provider Token、完整 `.env`
- 原始 Event Payload 输出前经过秘密字段过滤
- HTML / Markdown 预览默认转义，不执行脚本
- 图片使用受控 MIME 类型：Artifact 来自被测 Agent 可写的 Workspace，因此
  文件名和 `artifacts/manifest.json` 都是被评测方的输入。`text/html`、
  `image/svg+xml`、`application/xhtml+xml` 等浏览器会当作活动文档执行的类型
  一律降级为 `application/octet-stream`，MIME 不由 Run 自己声明
- Console v1 默认仅监听 `127.0.0.1`

## 25.1 写路径清单

"读取优先"不等于"只有一条写路径"。会改变机器状态的端点仅限以下几处，且都必须
是显式动作，不是读取的副作用：

```text
POST /api/v1/run-plans                 解析计划，不启动任何东西
POST /api/v1/run-plans/{id}/execute    启动真实 Docker Run
POST /api/v1/runs                      同上，直接创建
POST /api/v1/preflight                 在 runs_root 写入并删除探测文件
POST /api/v1/agents/{id}/check         启动真实容器与 Docker network
POST /api/v1/admin/reindex             重建 SQLite 索引
```

除此之外，`GET` 系列在索引过期时会**重建索引**（`catalog.ensure_current`）。
索引是可重建的派生数据，不是 Run 事实，因此这是允许的——但它意味着"只读请求"
仍会触碰 `EvalRuns/.console/`。

默认只监听回环地址；一旦用 `--host 0.0.0.0` 暴露，上述端点全部对网络开放，
当前**没有**认证、CSRF 或 Origin 校验。

## 26. 实施阶段

### Phase 0：架构和契约

- 固定本规格
- 增加架构依赖测试
- 定义持久化 Schema
- 建立 Legacy Fixture

验收：现有评测核心没有 Console 依赖。

### Phase 1：Catalog 和 Read Model

- LegacyRunReader
- RunV1Reader
- CatalogIndexer
- Run / Check / Event / Artifact / Comparison Query

验收：当前 EvalRuns 可完整索引，不扫描 UE5 缓存目录。

### Phase 2：只读 API

- Runs
- Run Detail
- Trace
- Evaluation
- Artifacts
- Comparison
- Registry

验收：API 可以表达真实 Codex、DSH 和 Evaluator Run。

### Phase 3：Console UI

- Design Tokens
- App Shell
- Runs
- Run Overview
- Trace
- Evaluations
- Artifacts
- Comparison
- Registry
- Run Launch Dialog：选择已有配置、Preview、Confirm、Job 状态

验收：用户可从失败 Run 一路定位到具体 Evidence 或 Trace Event，也可以从已有配置创建一次受控 Run。

### Phase 4：DCC 证据

- 图片、JSON、Diff Preview
- Blender / UE5 结构化证据组件
- Artifact Manifest 生产链

### Phase 5：Live 和 Run Control

- SSE
- Cancel / Rerun
- 多 Job 持久化和服务重启恢复
- 更细的实时 Docker / Evaluator 状态

启动 Run 前必须展示完全解析后的：

```text
Task
Agent
Model
Reasoning
MCP
Sandbox
Resources
Evaluator
```

### Phase 6：可选导出

- OpenInference
- Phoenix
- Inspect EvalLog Compatibility

均为可移除 Adapter，不进入 Console 核心依赖。

## 27. Console v1 验收标准

Console v1 完成需要同时满足：

1. Inspect Viewer 不再是标准用户入口。
2. 当前已有 Run 可被索引和查看，无需重新执行。
3. Run、Decision、Score、Check、Trace、Artifact、Comparison 均有稳定 API。
4. 前端没有 Codex / DSH 私有协议分支。
5. 分数可以追溯到 Check、Evaluator 和 Evidence。
6. 主 Agent Trace 与 Evaluator Trace 清晰分离。
7. Comparison 显示 invariant 是否一致。
8. UI 不递归扫描完整 Workspace。
9. API 不暴露宿主绝对路径和秘密。
10. 大 Trace 使用分页和虚拟列表。
11. 页面达到 WCAG AA 基础要求。
12. Playwright 完成主要调查流程。
13. `ai_native_evals` 的评测模块不引用 `ai_native_evals_console`；删除 Console 后评测 CLI 核心命令仍可运行。
14. 删除 Console 和 Catalog 后，评测核心仍可完整运行。

## 28. 明确拒绝的方案

### Fork Inspect Viewer

拒绝原因：升级和数据模型长期受 Inspect 内部实现约束，仍无法自然表达 Workspace、Evaluator Run 和 Comparison invariant。

### Phoenix 作为主界面

拒绝原因：其核心是 Trace / Evaluation Observability，不能直接承担 AI-Native 的 Workspace、Artifact、DCC Evidence 和 Profile Resolution 产品模型。

### Streamlit

拒绝原因：复杂 Trace、虚拟列表、分栏 Inspector、Artifact Preview 和长期交互架构受限。

### 浏览器直接读取 EvalRuns

拒绝原因：安全、性能、版本兼容和路径抽象无法保证。

### 一开始提供配置编辑

拒绝原因：配置编辑会引入写权限、秘密管理和配置版本恢复。Run 启动可以通过只接受已解析 Plan 的受控 API 提供，但不能让浏览器编辑 Profile 或拼接命令。

## 29. 后续扩展方向

本规格之外但接口必须允许：

- 多次重复实验的统计置信区间
- Agent × Model × Reasoning 的矩阵比较
- 人工评分和校准集
- Baseline / Regression 门禁
- Cost、Token、Latency 分析
- Trace 全文搜索
- 自动失败聚类
- Blender / UE5 可视化缩略图时间线
- 远程只读分享
- OpenInference / Phoenix 双写
- CI 评测报告

这些扩展均应建立在现有 Run、Event、Check、Artifact 和 Comparison 契约上，不改变 Evaluation Core 对 Console 零依赖的原则；CLI 分发入口不属于 Evaluation Core。
