# Manual run lifecycle

The evaluation suite keeps the user-facing configuration small and resolves it
into an immutable `run-manifest.json` before an Agent starts.

```powershell
# Create a snapshot and a clean run workspace. This does not start an Agent.
uv run ai-native-evals run prepare blender-cube --agent codex

# Inspect the resolved versions, model profile, paths, and snapshot status.
uv run ai-native-evals run status <run-id>

# Mark a prepared run ready/stopped while manual Agent lifecycle wiring is being added.
uv run ai-native-evals run start <run-id>
uv run ai-native-evals run stop <run-id>

# Remove a run workspace after reviewing its artifacts.
uv run ai-native-evals run cleanup <run-id>
```

Configuration defaults live in `config/eval.yaml`; secrets stay in the ignored
`config/.env.local`. `game-engine` and `AI-Native-DSH` are snapshotted into the
run directory, so a run records the requested ref, resolved commit, and dirty
state without modifying either source repository.
