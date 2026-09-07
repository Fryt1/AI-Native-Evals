# Run lifecycle

The evaluation suite keeps user-facing configuration small and resolves it into
an immutable run manifest before an Agent starts.

```text
resolve Task
    → create one run-id
    → snapshot project/DSH into workspace/
    → create output/scratch/evidence/trace
    → write run-manifest.json
    → run subject Agent in Docker
    → freeze subject Workspace
    → execute TestPlan checks
    → write check results and verdict
```

## Commands

```powershell
# Inspect the checks without creating a run
uv run ai-native-evals run plan <task-id>

# Prepare a run (snapshot only)
uv run ai-native-evals run prepare <task-id> --agent codex

# Start and wait manually
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run logs <run-id>
uv run ai-native-evals run wait <run-id>

# Evaluate the completed run's declarative TestPlan
uv run ai-native-evals run evaluate <run-id>

# Inspect a run and compare history
uv run ai-native-evals run digest <run-id>
uv run ai-native-evals run summary

# Remove a run only after reviewing its evidence
uv run ai-native-evals run cleanup <run-id>
```

## Workspace contract

Every test run gets a separate persistent Workspace:

```text
<run-dir>/workspace/
├── game-engine/       # source snapshot and subject Agent working tree
├── ai-native-dsh/      # DSH snapshot, when configured
├── output/             # candidate artifacts
├── scratch/            # disposable files
├── evidence/           # evaluator results
├── trace/              # event logs and evaluator traces
└── agent-config/       # run-scoped MCP files
```

The subject container mounts this directory as `/workspace`. Evaluator Agents
reuse the same host Workspace through read-only child mounts. A new run never
reuses another run's writable Workspace; an old run can only be provided as an
explicit read-only baseline.

## Reproducibility

The manifest records the resolved Task, model, protocol, reasoning effort, MCP
profile, snapshot commits/dirty state, Workspace paths, TestPlan, sandbox
limits, and runtime metadata. Prompt files, rubrics, evaluator IDs, and cache
versions are versioned separately and referenced by the Task plan.
