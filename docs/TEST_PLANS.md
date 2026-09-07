# Declarative Test Plans

A Task is a scenario; its tests are a `TestPlan` made of typed `CheckSpec`
entries. The plan is data, while reusable evaluator implementations remain
code-owned components.

```text
Task
└── TestPlan
    └── CheckSpec[]
        ├── outcome / quality / process
        ├── evaluator id
        ├── input evidence references
        ├── task-specific config
        ├── required/weight policy
        └── dependency edges
```

A check has these stable fields:

- `id`: unique name within the task
- `phase`: `outcome`, `quality`, or `process`
- `evaluator`: stable implementation id such as `script.text_equals.v1`
- `input`: evidence or workspace references consumed by the evaluator
- `config`: task-specific arguments
- `required`, `weight`, `on_error`, and `depends_on`: execution and policy data

The plan is validated before a run is prepared. Unknown dependencies, duplicate
IDs, invalid phases, and dependency cycles fail early. `ordered_checks()`
returns a stable topological order.

## Example

```yaml
test_plan:
  version: 1
  checks:
    - id: locate-hello-file
      phase: outcome
      evaluator: agent.artifact_locator.v1
      input:
        roots: [/workspace/evidence]
      config:
        artifact_kind: text_file
        expected_name: hello.txt
      required: true

    - id: hello-file-content
      phase: outcome
      evaluator: script.text_equals.v1
      input:
        artifact: locate-hello-file.selected_artifact
      config:
        expected_text: ai-native-codex-ok
      required: true
      depends_on: [locate-hello-file]
```

`prepare` copies the normalized plan into `run-manifest.json`. This makes the
exact tests and evaluator versions part of the reproducibility record. The
first executable inspection command is:

```powershell
uv run ai-native-evals run plan codex-file-smoke
```

`run evaluate <run-id>` now executes the registered evaluators against the
run's persisted Workspace/Evidence in dependency order. Agent-backed checks use
the shared Docker evaluator sandbox; script checks run at the host/verifier
boundary and write durable CheckResult files. The plan data model remains
separate from evaluator implementations.
