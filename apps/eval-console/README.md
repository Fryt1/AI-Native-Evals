# AI-Native Eval Console Web

React + TypeScript presentation layer for `AI-Native-Evals`.

The web app is intentionally read-only. It calls the FastAPI Console API and never reads `EvalRuns`, Docker, Codex, DSH, Blender, or UE5 directly.

## Development

From the repository root:

```powershell
pnpm --dir apps/eval-console install --frozen-lockfile
pnpm run dev
```

`pnpm run dev` is defined in the repository-root `package.json` and forwards to
this app. The Vite dev server runs on `http://127.0.0.1:5173` and proxies `/api`
to the Console API on port `8787`; start that API in a second terminal with
`uv run ai-native-evals console`. Vite prints a warning at startup when it is
not running.

## Build

```powershell
pnpm --dir apps/eval-console build
```

The production bundle is written to `apps/eval-console/dist/` and is served by:

```powershell
uv run ai-native-evals console
```

## Test

```powershell
pnpm --dir apps/eval-console test
```
