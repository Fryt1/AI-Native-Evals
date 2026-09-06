# Benchmark datasets

Keep benchmark cases separate from the Inspect AI runtime. Use these lanes:

- `development/` — cases used while iterating on adapters and tasks.
- `guardian/` — small regression set that every change must run.
- `holdout/` — reserved cases not used for prompt tuning.
- `challenge/` — recovery, ambiguity, interruption, and adversarial cases.

Expected acceptance metadata must not be included in Agent-visible task input.
