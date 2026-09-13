# Declarative Test Plans

A Task is a scenario; its tests are a `TestPlan` made of typed `CheckSpec`
entries. The plan is data, while reusable evaluator implementations remain
code-owned modules. New plans live in `tasks/<task-id>/task.yaml`; inline
`config/eval.yaml.tasks` remains a compatibility format only.

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
# tasks/my-task/task.yaml
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

## What decides a Run

Aggregation separates a **determinate** verdict from an **undetermined** one.
That distinction is the difference between "this Agent failed the task" and
"this task could not be measured" -- they must never collapse into the same
result, because a broken evaluator would otherwise be recorded as an Agent
scoring zero.

`on_error` says how an evaluator that could not run is read:

| `on_error` | check status | counts toward the verdict |
| --- | --- | --- |
| `fail` (default) | `error` | yes, as a determinate failure |
| `review` | `review` | no; the Run becomes undetermined |
| `skip` | `skipped` | no; the check is dropped from the verdict |

`uncertain_result` (`review` by default, or `fail`) decides what an undetermined
Run is reported as. Both error producers apply `on_error` identically: the
exception boundary in `_execute_check` and `_error_result`, which covers an
unregistered evaluator or a missing required config.

Scoring follows the same rule:

- `outcome_score` averages only the outcome checks that produced a determinate
  verdict, so an unmeasurable check yields `null` rather than a fabricated `0`.
- `quality_score` and `process_score` average the checks that returned a score.
- A Run with **no** determinate verdict at all is never reported as `pass`.

Decision order: hard-check failure, then the quality threshold, then
`uncertain_result`, otherwise `pass`.

