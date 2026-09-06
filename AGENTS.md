# AI-Native-Evals Agent Instructions

## Purpose

This repository is an evaluation suite built on the external `inspect-ai` package. It is not a fork of Inspect AI and must not become a second evaluation framework.

## Ownership

- `src/ai_native_evals/tasks/` owns task and dataset composition.
- `src/ai_native_evals/solvers/` owns adapters that launch or drive agents such as Codex and AI-Native-DSH.
- `src/ai_native_evals/scorers/` owns evaluation adapters, not the domain acceptance rules themselves.
- `src/ai_native_evals/sandboxes/` owns isolated workspaces and external DCC worker lifecycles.
- `src/ai_native_evals/adapters/` owns protocol and process translation at external seams.
- `AI-Native-Game-Engine` remains the source of truth for WorkflowPlan, Stage, Evidence, and deterministic acceptance.

## Non-negotiable evaluation rules

- Do not classify an Agent as successful from its final natural-language report.
- Use the same task fixture, tool surface, time limits, permissions, and scorer when comparing Agents.
- Record versions and hashes needed to reproduce a run.
- Keep expected answers and hidden acceptance metadata out of the Agent prompt.
- Keep Agent-specific logic inside a Solver or Adapter. Do not add Codex/DSH branches to shared scorers.
- Prefer objective world-state checks. Use LLM-as-Judge only for explicitly subjective dimensions and calibrate it against human labels.
- Keep the framework dependency pinned. Upgrade it deliberately and record the compatibility result.

## Inspect AI policy

- Use Inspect extension points before considering a fork.
- Do not copy Inspect source into this repository.
- A fork requires a written decision describing the missing upstream seam, the minimal patch, and the upstream contribution plan.

## Verification

```powershell
uv sync --dev
uv run pytest
uv run ruff check src tests
uv run inspect eval src/ai_native_evals/tasks/smoke.py@smoke --model mockllm/model
```
