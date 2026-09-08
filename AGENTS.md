# AI-Native-Evals Agent Instructions

## Purpose

This repository is an evaluation suite built on the external `inspect-ai` package. It is not a fork of Inspect AI and must not become a second evaluation framework.

## Ownership

- `tasks/<task-id>/` owns Task Bundle data: prompt, resources, TestPlan and optional Rubric.
- `profiles/agents/` owns Agent launch profiles; `profiles/models/` owns model/provider choices.
- `src/ai_native_evals/agents/` owns the stable AgentAdapter seam and registry.
- `src/ai_native_evals/adapters/` owns Codex/DSH protocol translation and normalized Agent events.
- `src/ai_native_evals/resources/` owns Task-declared resource providers and snapshots.
- `src/ai_native_evals/solvers/` owns Inspect Solver adapters; `agent_solver` must remain Agent-agnostic.
- `src/ai_native_evals/evaluation/` owns TestPlan execution and reusable Evaluator implementations.
- `src/ai_native_evals/scorers/` owns Inspect/domain scoring adapters, not task acceptance data.
- `src/ai_native_evals/runs/` owns per-run Workspace, Docker lifecycle, and evaluator sandboxes.
- `AI-Native-Game-Engine` remains the source of truth for WorkflowPlan, Stage, Evidence and deterministic acceptance.
- `dsh` is the real DeepSeek Harness source used to build the DSH Agent image; `AI-Native-DSH` is a separate project/plugin resource.

## Non-negotiable evaluation rules

- Do not classify an Agent as successful from its final natural-language report.
- Use the same Task Bundle, fixture, tool surface, time limits, permissions, and Evaluator when comparing Agents.
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
- Do not reintroduce `mcp_socket_call.py` or a custom Blender/UE5 socket client; use standard MCP declarations.
- A Task with `resources: []` must not create a Game Engine snapshot.

## Verification

```powershell
uv sync --dev
uv run pytest
uv run ruff check src tests
node --check .\docker\dsh-agent\acp-runner.mjs
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
```
