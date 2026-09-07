# Evaluation architecture

## Purpose

`AI-Native-Evals` is a thin evaluation suite on top of Inspect AI. It owns
benchmark tasks, run orchestration, Agent adapters, and evaluation adapters;
it does not replace Inspect's evaluation runner.

## Runtime and evaluation pipeline

```text
TaskSpec
  ├── task prompt
  ├── output/workspace contract
  └── TestPlan (typed CheckSpec[])
          │
          ▼
Run Orchestrator
  ├── per-run persistent Workspace
  ├── source/DSH snapshots
  ├── subject Agent Sandbox (read/write)
  └── subject event trace
          │
          ▼
Evidence Layer (Workspace is frozen for evaluation)
          ├── candidate artifacts
          ├── structural/test evidence
          └── evaluator traces
          │
          ├── Outcome Evaluator
          │     └── Locator Agent sandbox (read-only) → script verifier
          ├── Quality Evaluator
          │     └── Judge Agent sandbox (read-only) → versioned rubric
          └── Process Evaluator
                └── trace analyzer (+ optional Process Judge later)
          │
          ▼
Policy Aggregator
  ├── hard outcome/safety gates
  ├── quality threshold
  ├── review/uncertainty policy
  └── EvaluationReport / Verdict
```

All Agent-backed roles use the same `AgentSandboxRunner` contract. They receive
separate ephemeral containers, networks, gateways, traces, and permissions;
they do not share a writable container. Evaluator roles reuse the parent run's
Workspace through read-only mounts.

## Stable seams

```text
Inspect Task
    ├── Dataset       fixed task inputs and hidden benchmark metadata
    ├── Solver        one Agent adapter (Codex, DSH, or another Agent)
    ├── Sandbox       per-run Workspace and host/DCC worker lifecycle
    ├── TestPlan      task-owned checks and evaluator configuration
    └── Scorer        adapter from EvaluationReport to Inspect Score
```

`TestPlan` is data. Each `CheckSpec` names a reusable evaluator, its evidence
inputs, task-specific configuration, dependency edges, and failure policy.
Evaluator implementations do not contain Codex/DSH branches.

## The three evaluation methods

### Outcome

Outcome is `Agent-assisted discovery + deterministic verification`.
An independent Locator Agent identifies the candidate artifact in the frozen
Workspace. A resolver enforces allowed roots and provenance. A script or host
verifier decides objective conditions. Agent selection is evidence, not an
automatic pass.

### Quality

Quality uses an independent Judge Agent in the same sandbox framework. The
Judge reads a versioned prompt and task rubric, sees only the evidence allowed
by the Task, and emits structured status/score/confidence/criteria JSON. The
Judge cannot modify the candidate Workspace.

### Process

Process starts with objective event-trace telemetry: actions, failures,
retries, elapsed time, context reads, and verification behavior. A process
quality rubric or Process Judge can be added later after calibration. Process
metrics are diagnostic by default and do not override a hard outcome failure.

## Ownership

- `AI-Native-Game-Engine` remains the source of truth for WorkflowPlan, Stage,
  Evidence, Artifact, and deterministic domain acceptance.
- `AI-Native-DSH` remains an Agent runtime and does not depend on this suite.
- Codex and DSH are launched as external processes/protocol clients.
- Task prompts, Rubrics, and evaluator IDs are versioned; hidden acceptance
  metadata is not exposed to the subject Agent.
- Run evidence is stored under the per-run Workspace and is never inferred from
  the Agent's final natural-language report.

## Reproducibility metadata

Every real run records:

```text
task_id / task_version / test_plan_version
agent_id / agent_version / model_id
inspect_ai_version / evals_commit
game_engine_commit / dsh_commit
fixture_hash / tool_surface_version
Blender_version / UE5_version / OS
seed / timeout / permissions
prompt_version / rubric_version / evaluator_version
```

## Fork policy

Do not fork Inspect AI for normal integration work. First use Task, Solver,
Scorer, Sandbox, custom evaluator, and package entry points. A fork requires a
written record of the missing upstream seam, a minimal patch, tests, and an
upstream contribution plan.
