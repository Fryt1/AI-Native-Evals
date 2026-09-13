# Codex / DSH 同 Task 实跑结果

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

两个 Run 的现场都在本机 `<EvalRuns>/` 下（具体位置见 `config/eval.yaml` 的
`paths.runs_root`），目录名即 Run ID：

```text
<EvalRuns>/structured-report-contract-ff8e08ac2a/
<EvalRuns>/structured-report-contract-0b8400ec2b/
```

每个现场都包含：

```text
workspace/output/agent-result.json
workspace/evidence/checks/*.json
workspace/evidence/evaluation.json
workspace/trace/agent-container.log
workspace/trace/normalized-events.jsonl
workspace/trace/digest.json
workspace/trace/evaluators/
```

## 解释

`dsh-release` 是为了快速验证真实 DSH Docker 链路而使用的发布镜像；需要严格复现某个 DSH 源码 commit 时，改用源码镜像：

```powershell
pwsh -File .\tools\build-sandbox-images.ps1 -IncludeDsh -UseMirror
```

两种镜像共用同一套 Adapter/Workspace/Trace/Evaluator 契约；差别只在 DSH 镜像来源，不改变评测架构。
