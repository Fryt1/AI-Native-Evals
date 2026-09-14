# Experiment definitions

One file per experiment. A definition states the **design** rather than a command
line: `vary` is what changes between runs, `fixed` is what is held constant, and
`repeats` is how many attempts each combination gets.

```powershell
uv run ai-native-evals experiment list
uv run ai-native-evals experiment show agent-comparison
uv run ai-native-evals experiment run agent-comparison
uv run ai-native-evals experiment run agent-comparison --runs 10
```

The axes under `vary` form a Cartesian product, so two axes produce every
combination — a hand-written list of conditions can silently omit one. Every key
*not* under `vary` is frozen and recorded in the run's `invariant`, because a
result whose held-constant inputs are unstated cannot be reproduced.

## Why `repeats` matters

One attempt cannot show variance. The same Agent on the same Task passes or fails
on different attempts, so a single-attempt table reports sampling noise as if it
were a difference between Agents. With `repeats > 1` the result carries a pass
rate, a 95% Wilson interval, and an explicit statement of whether the sample can
separate the cells at all.

If the intervals overlap the run exits **2** and says so, rather than presenting
the higher pass rate as a winner.

Only attempts that produced a decision (`pass`/`fail`) count toward the pass
rate. An attempt that could not start, or that ended `review`, is reported as
**Unmeasured** and excluded — infrastructure failure is not Agent behaviour.

## Varyable axes

```text
agent  model_profile  provider  model  reasoning_effort
mcp_profile  sandbox_profile  preset  game_engine_ref  dsh_ref
```

Anything else is refused at load time, so a typo cannot silently freeze a value
that was meant to vary.

## Relationship to `compare`

`ai-native-evals compare <task> --agents a,b --runs N` is this same machinery with
one varying axis. It is kept as its own command because "which Agent is better"
is the question asked most often. For two or more axes at once, or to keep a
design under version control, write a definition here.
