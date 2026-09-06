# Evaluation architecture

## Purpose

`AI-Native-Evals` is a thin evaluation suite on top of Inspect AI. It owns
benchmark tasks and adapters; it does not replace Inspect's evaluation runner.

## Stable seams

```text
Inspect Task
    ├── Dataset       fixed task inputs and hidden benchmark metadata
    ├── Solver        one Agent adapter (Codex, DSH, or another Agent)
    ├── Sandbox       isolated Windows/DCC or future remote worker
    └── Scorer        AI-Native-Game-Engine verifier adapter
```

The first package slice contains a model-free `smoke` task. It proves that the
Inspect Task → Solver → Scorer composition works before external Agent and DCC
processes are introduced.

## Ownership

- `AI-Native-Game-Engine` remains the source of truth for Stage, Evidence,
  Artifact, and deterministic acceptance.
- `AI-Native-DSH` remains an Agent runtime and must not depend on this suite.
- Codex and DSH are launched as external processes or protocol clients; they are
  not imported into shared evaluation logic.
- Expected acceptance metadata stays outside the Agent-visible task prompt.

## Planned adapters

### Codex

Launch the selected Codex CLI or protocol client in an isolated `run_dir` and
capture its transcript and machine-readable run manifest.

### DSH

Launch the selected AI-Native-DSH profile through CLI, ACP, or JSON-RPC. Capture
its session events and the same run manifest fields as Codex.

### Domain verifier

Invoke the Game Engine verifier after the Agent exits. The verifier writes an
`ainative-verdict.json` payload containing the final status, score, Stage
results, Evidence facts, and failure classification. The Scorer reads that
payload; it does not infer success from Agent prose.

## Reproducibility metadata

Every real run should record:

```text
task_id / task_version / dataset_version
agent_id / agent_version / model_id
inspect_ai_version / evals_commit
game_engine_commit / dsh_commit
fixture_hash / tool_surface_version
Blender_version / UE5_version / OS
seed / timeout / permissions
```

## Fork policy

Do not fork Inspect AI for normal integration work. First use Task, Solver,
Scorer, Sandbox, Hook, and package entry points. A fork requires a written
record of the missing upstream seam, a minimal patch, tests, and an upstream
contribution plan.
