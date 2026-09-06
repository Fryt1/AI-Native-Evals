# AI-Native-Evals

基于 [Inspect AI](https://inspect.aisi.org.uk/) 的 Agent 评测套件，用于在同一组任务和真实世界验收规则下比较 Codex、AI-Native-DSH 以及未来的其他 Agent。

本仓库**不是** Inspect AI 的 fork，也不重新实现评测 Runner。Inspect AI 是外部依赖；本仓库只维护：

- 评测任务集（Task / Dataset）
- Agent 适配器（Solver）
- Blender/UE5 环境适配（Sandbox / Worker）
- AI-Native-Game-Engine 领域评分器（Scorer）
- 实验条件、失败分类和报告入口

## 架构

```text
Inspect AI
    ↓
AI-Native-Evals
    ├── Task / Dataset
    ├── Codex Solver
    ├── DSH Solver
    ├── Windows DCC Sandbox
    └── Game Engine Scorer
            ↓
        StageResult / Evidence / Artifact
```

## 目录

```text
src/ai_native_evals/
├── tasks/       Inspect Task 定义
├── solvers/     被测 Agent 接入
├── scorers/     领域结果验收
├── sandboxes/   工作区和 DCC 环境
└── adapters/    外部进程、协议和日志适配

datasets/        development / guardian / holdout / challenge
fixtures/        可复现的初始工作区
experiments/     预定义实验条件
```

## 快速开始

使用 uv：

```powershell
uv sync --dev
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
```

查看帮助：

```powershell
uv run inspect eval --help
```

当前 `smoke` task 不调用真实模型，只验证 Inspect Task、Solver、Scorer 三个接口能够组合运行。`codex_file_smoke` 已接入本机 Codex CLI：它在独立 `run_dir` 创建 `hello.txt`，然后由文件状态 Scorer 验收。DSH 和真实 Blender/UE5 环境会在后续提交中接入。

## 评测原则

1. Agent 是可替换的黑盒 Solver。
2. 任务成功由真实世界状态、Evidence 和 Game Engine Scorer 判定，不由 Agent 自述判定。
3. Codex 与 DSH 使用相同任务、Fixture、工具面、限制和评分器时，结果才具有可比性。
4. 每次运行记录 Inspect 版本、Agent 版本、模型、Prompt/Profile、Game Engine commit、Fixture hash 和环境版本。
5. 评测集分为 development、guardian、holdout、challenge，避免针对固定样本过拟合。

## 当前状态

这是第一版骨架。它刻意不包含：

- Inspect AI 源码副本
- 自动调用 UE5/Blender 的隐藏调度器
- 复制 Game Engine 的 Stage 验收逻辑
- 只依赖自然语言或 LLM-as-Judge 的主评分


## First real Agent smoke

Run the real Codex smoke task from the repository root:

```powershell
uv run inspect eval src/ai_native_evals/tasks/codex_file_smoke.py@codex_file_smoke --model mockllm/model
```

The run writes `codex-events.jsonl`, `codex-stderr.log`, `codex-last-message.txt`, and `run-manifest.json` under `runs/codex-file-smoke/<run-id>/`. The scorer checks the actual `hello.txt` bytes; it does not trust the final Agent message. Codex lifecycle, command execution, tool results, Agent messages, and file-change events are also projected into the Inspect Messages/Transcript view.
