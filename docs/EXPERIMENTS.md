# Experiments: repeated runs and run matrices

A single evaluation run is one sample. The same Task and the same Agent pass or
fail on different attempts — the model samples, and a container occasionally
fails to start. A table of one run per Agent therefore reports sampling noise as
if it were a difference between Agents, and a verdict drawn from it is not a
verdict.

An **experiment** is a declared design that fixes this: what varies, what is held
constant, and how many attempts each combination gets.

## Two ways to ask

```powershell
# One axis: which Agent is better at this Task.
uv run ai-native-evals compare codex-file-smoke --agents codex,dsh-release --runs 5

# Any number of axes: write a definition under experiments/.
uv run ai-native-evals experiment list
uv run ai-native-evals experiment show reasoning-effort-sweep
uv run ai-native-evals experiment run reasoning-effort-sweep
```

`compare` is not a second implementation. It builds an `ExperimentSpec` with
`agent` as its only varying axis and hands it to the same runner, so the
statistics and the inconclusive-sample verdict are identical either way.

## The definition

```yaml
# experiments/reasoning-effort-sweep.yaml
id: reasoning-effort-sweep
task_id: codex-file-smoke
vary:
  agent: [codex]
  reasoning_effort: [low, high]
fixed:
  model: gpt-5.6-luna
  mcp_profile: none
repeats: 3
```

| key | meaning |
| --- | --- |
| `task_id` | the Task Bundle being measured |
| `vary` | selectors that change between cells; the axes form a Cartesian product |
| `fixed` | selectors held constant, recorded in the run's invariant |
| `repeats` | attempts per cell |

Two axes with two values each produce **four** cells. Writing the conditions by
hand can silently omit one; a product cannot.

### Varyable axes

```text
agent  model_profile  provider  model  reasoning_effort
mcp_profile  sandbox_profile  preset  game_engine_ref  dsh_ref
```

Anything else is refused when the definition loads, so a typo cannot silently
freeze a value that was meant to vary. Varying and fixing the same selector is
refused for the same reason.

## What the result reports

```text
| Cell                              | Passed | Pass rate | 95% CI      | Unmeasured |
|-----------------------------------|--------|-----------|-------------|------------|
| agent=codex · reasoning_effort=low| 2/3    | 67%       | 0.21–0.94   | 0          |
| agent=codex · reasoning_effort=high| 3/3   | 100%      | 0.44–1.00   | 0          |
```

Per cell: a pass rate, a **Wilson** 95% interval, the unmeasured count, and each
score as a median with its observed range — never as a single point.

### Why Wilson, not the normal approximation

With five attempts, the normal interval collapses to zero width at `5/5`,
claiming certainty from five observations, and can extend below zero at `0/5`.
Wilson stays inside `[0, 1]` and keeps its width at the boundaries, which is
exactly where a small experiment lives.

### The denominator

Only attempts that produced a decision (`pass`/`fail`) count toward the pass
rate. An attempt that never started, or that ended `review`, is reported as
**Unmeasured** and excluded. Infrastructure failure is not Agent behaviour;
counting it as a failure would let a flaky machine look like an incompetent
Agent. The count is always shown, so a shrunken sample is visible.

### The verdict

The report states whether the cells are actually distinguishable:

- **可区分** — every pair of intervals is disjoint.
- **无法区分** — some pair overlaps, so this sample cannot separate them,
  whatever the pass rates look like.

`experiment run` and `compare --runs N` exit **2** in the second case. Ran fine,
proves nothing: a distinction that matters for CI, so a pipeline does not treat
an inconclusive experiment as a green light.

## Where results live

```text
EvalRuns/
├── comparisons/<id>/comparison.json    # written by `compare`
└── experiments/<id>/experiment.json    # written by `experiment run`
```
Both carry the same facts, and the Console indexes **both folders** through its
existing comparison reader (`comparison_id`, `runs`), so an experiment shows up in
the UI with no Console change. Each experiment artifact also carries `cells` (the
aggregated view) and `discrimination` (the verdict).

## Cost and concurrency

`attempts = cells x repeats`, and every attempt is a real Docker run plus real
model calls. `experiment show` prints the total before you commit to it:

```text
2 cell(s) x 5 = 10 run(s)
```

Attempts are independent — each gets its own run id, workspace, network and
containers — so they run in parallel:

```powershell
uv run ai-native-evals experiment run agent-comparison --runs 20 -j 8
```

Measured on this machine, against this Task:

```text
serial (1 at a time)    93.0 s per attempt
-j 4                   3.11x
-j 8                   5.40x
```

An attempt spends nearly all of its wall clock waiting on the model, and a
running attempt measures around **60 MiB** resident (agent ~37 MiB + gateway
~22 MiB), with CPU essentially idle. The sandbox's `memory: 8g` is a ceiling, not
a reservation — so the limit is your provider's rate tolerance and WSL's total
memory, not cores.

The default is `min(4, attempts)`: enough to hide model latency, few enough not to
arrive as a burst. The effective value is recorded in the artifact, because a
result should say how its attempts were scheduled.

Two properties are guaranteed regardless of `-j`:

- **The report is ordered by design**, not by finish order. Attempt *n* is row *n*
  whatever completed first.
- **One attempt cannot sink the batch.** A failed or crashed attempt is recorded
  as an `error` row and excluded from the pass rate, exactly as in serial.

### Reclaiming after an interruption

A killed process (Ctrl-C, a closed terminal) never reaches its cleanup, so its
containers and network stay behind:

```powershell
uv run ai-native-evals reclaim --dry-run   # list what is orphaned
uv run ai-native-evals reclaim             # remove it
```

Ownership is decided by matching each Docker resource against the run ids on
disk, and a run's resources are only touched once it is finished *and* its
manifest has been quiet for 15 minutes (`AI_NATIVE_EVALS_RECLAIM_GRACE_SECONDS`
overrides that). The quiet period matters: a run's status turns terminal before
its evaluator sandbox has cleaned up, so acting on status alone could pull
containers out from under a live evaluation.
