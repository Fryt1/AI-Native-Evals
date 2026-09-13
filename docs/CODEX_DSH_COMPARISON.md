# Codex / DSH 同 Task 实跑结果

> **历史记录。** 这里引用的 `structured-report-contract` Task Bundle 与两个 Run
> 现场已在 `404ff92` 中随其余退化 Task 一并删除，`tasks/` 现只保留
> `codex-file-smoke` 与 `blender-scene-build`。本文保留当时的结论与 Run ID，因为
> 它记录的是**当时真实发生过的事**；改写 Run ID 去迁就当前的目录树会让记录失真。
> 要复现同样的对比，请用现存的 Task 重跑，见文末。

在 **2026 年 9 月 8 日**，使用同一 `structured-report-contract` Task、同一模型 Profile、同一 Gateway、同一 TestPlan 和同一 Outcome/Quality Evaluator，分别运行了两个 Docker Agent：

| Agent | 镜像 | Run | Outcome | Quality | Process | Decision |
|---|---|---|---:|---:|---:|---|
| Codex | `ai-native-codex-agent:local` | `structured-report-contract-ff8e08ac2a` | 1.0 | 1.0 | 1.0 | pass |
| DSH release (`@deepseek-ai/dsh@0.1.2-rc.1`) | `ai-native-dsh-agent:release` | `structured-report-contract-0b8400ec2b` | 1.0 | 1.0 | 0.833 | pass |

## 这次运行证明了什么

1. Task 与 Agent 已经解耦：同一个 Task 只用 `--agent` 切换被测 Agent。
2. Codex 使用 Responses 入口，DSH 使用标准 ACP `initialize/session/new/session/prompt/session/close`，但都能写入同一个 Run Workspace。
3. DSH 的标准 ACP `McpServer[]` 配置能被容器接收；本例没有 MCP，但协议链路、模型配置、写文件和关闭会话都真实通过 Docker 完成。
4. 两个 Agent 的 Outcome 和 Quality 结果都由真实 Workspace/Evidence 检查，不依赖最终自然语言报告。
5. Process Scorer 能看到差异：本次 DSH 曾有一次文件工具 `chmod` 失败，后来通过另一条写入路径完成任务，因此最终 Outcome 通过、Process 为 `0.833`。这正是“结果完成”和“过程质量”分开的预期行为。

## 现场位置

两个 Run 的现场**已不存在**（随 Task Bundle 一并删除，见文首说明）。它们曾经位于：

```text
<EvalRuns>/structured-report-contract-ff8e08ac2a/     # 已删除
<EvalRuns>/structured-report-contract-0b8400ec2b/     # 已删除
```

每个现场当时包含：

```text
workspace/output/agent-result.json
workspace/evidence/checks/*.json
workspace/evidence/evaluation.json
workspace/trace/agent-container.log
workspace/trace/normalized-events.jsonl
workspace/trace/digest.json
workspace/trace/evaluators/
```

## 复现同样的对比

用现存的 Task 重跑，被测 Agent 由 `--agents` 切换，Task/TestPlan/Evaluator 不变：

```powershell
uv run ai-native-evals compare codex-file-smoke --agents codex,dsh-release --preset codex-default
```

## 解释

`dsh-release` 是发布镜像，也是当前唯一受支持的 DSH Agent：

```powershell
.\tools\eval.ps1 build -Agent dsh-release -UseMirror
```

曾经有一个从 DSH 源码构建的镜像，用来复现某个源码 commit。它已移除：workspace 的 `lib/` 编译产物需要把 `tests`、`website`、`benchmarks` 全部放进构建上下文再跑一次全量 `tsc`，而本仓库并不修改 DSH 的源码，这样做的代价没有对应需求。自研插件改由 profile 的 `attach` 在运行时投递，同样不需要源码镜像。
