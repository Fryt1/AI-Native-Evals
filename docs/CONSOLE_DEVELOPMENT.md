# AI-Native Eval Console 开发指南

## 目标

Console 是 AI-Native-Evals 的唯一正式可视化前端。它可以通过受控的 Run Plan API 发起评测，但不直接修改 Codex、DSH、Blender 或 UE5 配置，也不接受任意 shell 命令。

## 启动

在仓库根目录执行：

```powershell
pnpm install --frozen-lockfile     # workspace 根命令，会一并安装 apps/eval-console
pnpm build
uv run ai-native-evals console
```

打开 `http://127.0.0.1:8787/`。

也可以使用：

```powershell
.\tools\console.ps1
```

如果已经构建过前端：

```powershell
.\tools\console.ps1 -SkipBuild
```

## 重新索引

```powershell
uv run ai-native-evals console reindex
```

索引位于仓库外配置的 `EvalRuns/.console/catalog.sqlite`，它可以删除后重建，不是评测事实来源。

## 开发前端

```powershell
pnpm run dev
```

在仓库根目录执行即可（根 `package.json` 的 `dev` 转发到 `apps/eval-console`）。
Vite 使用 `127.0.0.1:5173`，将 `/api` 代理到 `8787`。先在另一个终端启动 FastAPI Console：

```powershell
uv run ai-native-evals console
```

Vite 启动时会探测 Console API，不可达会直接打印提示，避免出现"页面能打开但全空"。
注意：`node_modules` 里是 Windows 原生二进制，请在 Windows 侧（PowerShell / cmd）执行
pnpm，不要在 WSL 里跑，否则会报 `Cannot find native binding`。

## 后端边界

后端读取顺序：

```text
run-manifest.json
    ↓
evaluation.json / verdict.json
    ↓
digest.json / normalized-events.jsonl
    ↓
workspace/artifacts/manifest.json
```

旧 Run 没有 `normalized-events.jsonl` 时，允许从 `trace/digest.json` 的 `timeline` 生成 legacy Event；不能伪造缺失的时间戳、Turn 或 Step。

前端只使用 API DTO：

```text
RunSummary
RunDetail
AgentEvent
Check
Artifact
ComparisonDetail
Registry
RunPlan
RunJob
```

执行测试必须遵循：

```text
POST /api/v1/run-plans → 展示解析结果 → POST /api/v1/run-plans/{plan_id}/execute
```

不要在 React 中解析 Codex JSONL、DSH ACP 或 Inspect `.eval`。

## 测试

后端：

```powershell
uv run pytest tests/test_console_api.py
uv run ruff check src tests
```

前端：

```powershell
pnpm test
pnpm build
```

完整验证：

```powershell
uv run pytest
uv run ruff check src tests
pnpm test
pnpm build
```

## 页面原则

- 首先显示结果，再显示原因，最后显示原始细节。
- 每个分数必须能追溯到 Check、Evaluator、Evidence 或 Artifact。
- `null` 分数显示“未评测”，不能显示成 0。
- `status` 表示生命周期，`decision` 表示评测结论，两者不能混用。
- 主 Agent Trace 与 Evaluator 子 Trace 必须清晰区分。
- Comparison 必须告诉用户公平性是否已验证。
- 大日志使用分页和按需加载；不要把整个 Workspace 发送给浏览器。
- UI 只能选择 Registry 中已有的 Profile ID；不能编辑 Profile 内容或拼接命令。
- Job 在后台执行，关闭弹窗只能让它继续后台运行，不能默认取消真实评测。
