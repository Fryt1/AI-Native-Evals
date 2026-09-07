# Evaluation pipeline

The evaluation suite uses one persistent Workspace per test run and a declarative
TestPlan. Agent-backed evaluators use the same Docker sandbox runner as the
subject Agent, but each role receives an independent container and explicit
read-only mounts.

```text
TaskSpec
  → prepare run
  → persistent Workspace
  → subject Agent sandbox (read/write)
  → freeze subject run
  → TestPlanRunner
       ├─ Outcome: Locator Agent (read-only sandbox) → script verifier
       ├─ Quality: Judge Agent (read-only sandbox) → rubric score
       └─ Process: trace analyzer (optional Process Judge later)
  → EvaluationReport
  → PASS / FAIL / REVIEW
```

## Run layout

```text
EvalRuns/<run-id>/
└── workspace/
    ├── game-engine/       # per-run project snapshot and Agent working tree
    ├── ai-native-dsh/      # per-run DSH snapshot, when present
    ├── output/             # candidate outputs
    ├── scratch/            # temporary working files
    ├── evidence/           # evaluator-owned check results and verdicts
    ├── trace/              # subject and evaluator Agent event logs
    └── agent-config/       # run-scoped MCP configuration
```

The Docker subject container mounts this whole `workspace` at `/workspace` and
runs with `/workspace/game-engine` as its working directory. Host DCCs should
write through the resolved Windows Workspace path recorded in the manifest.

After the subject exits, the Workspace is treated as frozen. Outcome and
Quality evaluators mount the relevant children read-only. They may write only
their own role trace and result files.

## TestPlan and CheckSpec

A Task declares data; evaluator implementations are reusable code:

```yaml
test_plan:
  version: 1
  checks:
    - id: locate-result
      phase: outcome
      evaluator: agent.artifact_locator.v1
      input:
        roots: [/workspace/output]
      config:
        artifact_kind: result
        expected_name: result.json
      required: true

    - id: result-valid
      phase: outcome
      evaluator: script.schema_validator.v1
      input:
        artifact: locate-result.selected_artifact
      config:
        schema: schemas/result.json
      required: true
      depends_on: [locate-result]
```

`TestPlan` validates check IDs, evaluator IDs, phases, dependency references,
cycles, error policy, weights, and quality threshold before the run starts.
`prepare` stores the normalized plan in `run-manifest.json`, making the exact
checks reproducible.

Inspect a plan without starting an Agent:

```powershell
uv run ai-native-evals run plan codex-file-smoke
```

## Evaluator roles

### Outcome

`agent.artifact_locator.v1` runs an Agent in Docker to identify a candidate
artifact. It returns a container-visible path. The resolver maps it to the
host Workspace and checks allowed roots, existence, and provenance. A script
verifier then reads or executes the artifact. Locator output is evidence about
selection, not permission to pass; the script remains the final authority for
objective conditions.

### Quality

`agent.quality_judge.v1` runs an independent, read-only Agent with a versioned
prompt and Task rubric. Prompt and rubric are loaded from repository files and
the Judge must return structured JSON with status, score, confidence, criteria,
and evidence references.

### Process

`trace.process_analyzer.v1` reads the subject Agent event stream and records
observable metrics. The current process score is intentionally provisional and
is not a hard task gate; process quality rules should be calibrated from real
runs before they affect pass/fail.

## Commands

```powershell
# Resolve and snapshot a Task
uv run ai-native-evals run prepare <task-id> --agent <agent>

# Run the subject Agent in Docker
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run wait <run-id>

# Execute the TestPlan after the subject run is complete
uv run ai-native-evals run evaluate <run-id>

# Inspect process and history
uv run ai-native-evals run digest <run-id>
uv run ai-native-evals run summary
```

The durable results are:

```text
workspace/evidence/checks/<check-id>.json
workspace/evidence/evaluation.json
workspace/evidence/verdict.json
workspace/trace/evaluators/<role>/
```

A real proof run is
`EvalRuns/roundtrip-blender-ue5-v3-be18d6b98e`: the subject Codex ran in
Docker, the Outcome Locator ran in a separate Docker sandbox, Blender and UE5
were read back independently by scripts, and the final TestPlan decision was
`pass`.
