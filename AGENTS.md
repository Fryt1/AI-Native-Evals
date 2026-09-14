# AI-Native-Evals Agent Instructions

## Purpose

This repository is an evaluation suite built on the external `inspect-ai` package. It is not a fork of Inspect AI and must not become a second evaluation framework.

## Ownership

- `tasks/<task-id>/` owns Task Bundle data: prompt, resources, TestPlan and optional Rubric.
- `experiments/<id>.yaml` owns experiment *definitions* (what varies, what is frozen, how many repeats); `src/ai_native_evals/experiments/` owns loading, validation, the run matrix, and the statistics. Definitions are data and belong in git; do not add a flag per axis to `compare` instead.
- `src/ai_native_evals/runs/compare.py` is the single-axis case of an experiment and must delegate to the experiment runner. It may not grow its own execution loop, statistics, or artifact shape.
- `profiles/agents/` owns Agent launch profiles; `profiles/models/` owns model/provider choices.
- `src/ai_native_evals/agents/` owns the stable AgentAdapter seam and registry.
- `src/ai_native_evals/adapters/` owns Codex/DSH protocol translation and normalized Agent events.
- `src/ai_native_evals/resources/` owns Task-declared resource providers and snapshots.
- `src/ai_native_evals/solvers/` owns Inspect Solver adapters; `agent_solver` must remain Agent-agnostic.
- `src/ai_native_evals/evaluation/` owns TestPlan execution and reusable Evaluator implementations.
- `src/ai_native_evals/scorers/` owns Inspect/domain scoring adapters, not task acceptance data.
- `src/ai_native_evals/runs/` owns per-run Workspace, Docker lifecycle, and evaluator sandboxes.
- `AI-Native-Game-Engine` is the source of truth for WorkflowPlan, Stage, Checklist and Evidence *shape*. It is **not** the source of truth for whether a task was completed: its `StageAcceptanceEvaluator` grades the Agent against a checklist the Agent itself authored, so a Stage `pass` only proves internal consistency. Task-level verdicts belong to this repository (see the Acceptance boundary section).
- `dsh` is the real DeepSeek Harness source used to build the DSH Agent image; `AI-Native-DSH` is a separate project/plugin resource.

## Acceptance boundary

`AI-Native-Game-Engine` and this repository both check world state, at different
levels. Do not collapse them.

- **Game Engine** runs a `StageAcceptanceEvaluator` at each Stage boundary, using
  the `execution_checklist` / `acceptance_checklist` frozen inside the Agent's own
  WorkflowPlan. It answers "did this Stage meet the plan's own criteria?" and gates
  progress inside one run.
- **This repository** runs a Task's TestPlan after the run, using criteria written
  by the Task author. It answers "was the task actually completed?" and is the only
  verdict valid outside the run.

Rules:

- Never accept a Stage `pass` as this repository's `pass`. Because the Agent authors
  its own checklist, doing so would let the subject grade itself -- the same mistake
  as trusting its final natural-language report.
- `StageAcceptance` may be read as a *process signal* only (how well the Agent
  planned and evidenced its own work). It is not evidence of outcome.
- The vocabularies are not interchangeable: Game Engine uses
  `pass` / `warn` / `fail` / `unknown` / `needs_human`; a Check here uses
  `passed` / `failed` / `review` / `blocked` / `error` / `skipped` / `observed`.
  When a Game Engine status is surfaced in the API or UI, map it explicitly.
- Primitive operators (`exists`, `equals`, `within_tolerance`, ...) exist on both
  sides for their own layers. Adding one on the Game Engine side does not
  automatically require one here, and vice versa -- but check before assuming a new
  primitive is already covered.

## Non-negotiable evaluation rules

- Do not classify an Agent as successful from its final natural-language report, nor from the workflow's own self-authored Stage acceptance.
- Use the same Task Bundle, fixture, tool surface, time limits, permissions, and Evaluator when comparing Agents.
- Never draw a conclusion about an Agent from a single run. An evaluation is a sample: report a pass rate with its interval, keep infrastructure failures out of the denominator, and say plainly when the sample cannot separate the conditions. `docs/EXPERIMENTS.md` defines the contract.
- Record versions, hashes, Profile, model and resource snapshots needed to reproduce a run.
- Keep expected answers and hidden acceptance metadata out of the subject Agent prompt.
- Keep Agent-specific logic inside a Profile, Solver or Adapter. Do not add Codex/DSH branches to shared Evaluators.
- Prefer objective world-state checks. Use LLM-as-Judge only for explicitly subjective dimensions and calibrate it against human labels.
- All subject and evaluator Agents run in Docker; Windows Blender/UE5 remain host services reached through standard MCP.
- Keep raw protocol traces and write a normalized `normalized-events.jsonl` for Process scoring/viewing.
- Keep the framework dependency pinned. Upgrade it deliberately and record the compatibility result.

## Inspect AI policy

- Use Inspect extension points before considering a fork.
- Do not copy Inspect source into this repository.
- A fork requires a written decision describing the missing upstream seam, the minimal patch, and the upstream contribution plan.

## Safety and secrets

- Never read or mount the host `~/.codex` or personal DSH home into a run.
- Never commit `config/.env.local`, API keys, cache archives, or `EvalRuns/`.
- Never commit large binaries. `cache/docker/sandbox-images.tar` is ~2.7 GB and
  `cache/codex/*.tgz` are agent tarballs; only their hash-checked metadata
  (`cache/manifest.json`, `cache/**/requirements.lock`, `cache/codex/VERSION`)
  belongs in Git. Those paths are ignored: do not add un-ignore rules for them,
  and never `git add -f` anything under `cache/`. Run
  `pwsh -File tools/check-git-hygiene.ps1` after any bulk `git add`; it fails on
  ignored-but-tracked paths and on any tracked file over 5 MB.
- Do not reintroduce `mcp_socket_call.py` or a custom Blender/UE5 socket client; use standard MCP declarations.
- A Task with `resources: []` must not create a Game Engine snapshot.

## Verification

```powershell
uv sync --dev
uv run pytest
uv run ruff check src tests
node --check .\docker\dsh-agent\acp-runner.mjs
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
pwsh -File tools/check-git-hygiene.ps1
```
## Eval Console rules

- `src/ai_native_evals_console/` is a projection-first API boundary: reads are the default, and every state-changing endpoint is an explicit action rather than a side effect of a read. The full list is in `docs/EVAL_CONSOLE_SPEC.md` §25.1; add to it when you add one. Evaluation modules must not import it (`tests/test_console_api.py` walks every owned module and enforces this); the top-level CLI is the only exception, dispatching the optional console command.
- An artifact's media type is decided by the Console, never by the run. A run's workspace and its `artifacts/manifest.json` are written by the evaluated Agent, so a declared `mime_type` is subject input: types a browser executes as a document are downgraded before they are served.
- `apps/eval-console/` is the only formal UI. It calls `/api/v1` and must not read EvalRuns or provider-specific Codex/DSH logs directly. Its source is tracked; `dist/` and the copied `src/ai_native_evals_console/static/` are build output.
- `apps/eval-console` is a pnpm workspace member declared in the root `pnpm-workspace.yaml`. Keep the root `pnpm install` working: without that file the root manifest has no dependencies, so pnpm reports success and installs nothing.
- EvalRuns remain the source of truth. `EvalRuns/.console/catalog.sqlite` is a rebuildable index and must not be committed.
- Console API responses must not expose host absolute paths, secrets, personal Agent homes, or arbitrary file access.
- Use `pnpm install && pnpm test && pnpm build` (root) after UI changes.
