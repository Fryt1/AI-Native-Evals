"""Compare several Agents on one Task.

This is an experiment with a single varying axis (`agent`). It exists as its own
command because "which Agent is better at this Task" is the question asked most
often, and spelling it as a full experiment definition every time would be
ceremony. It is not a second implementation: it builds an `ExperimentSpec` and
hands it to the same runner, so the statistics, the frozen-input record and the
"this sample cannot tell them apart" verdict are identical however the
experiment was asked for.

For anything with more than one axis -- a provider sweep, a reasoning-effort
sweep, two axes at once -- write an experiment definition under `experiments/`
and use `ai-native-evals experiment run`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .runner import (
    ExperimentRunError,  # noqa: F401 - re-exported for callers
    render_experiment_markdown,
    run_experiment,
)
from .spec import ExperimentSpec


class ComparisonError(RuntimeError):
    """Raised when comparison setup cannot be completed."""


#: Selectors a comparison may pass through. Kept explicit so a keyword that is
#: silently dropped cannot look like a frozen input.
_PASSTHROUGH = (
    "model_profile",
    "provider",
    "model",
    "reasoning_effort",
    "mcp_profile",
    "sandbox_profile",
    "preset",
    "game_engine_ref",
    "dsh_ref",
)


def compare_task(
    repo_root: Path,
    task_id: str,
    agents: Iterable[str],
    *,
    config_path: Path | None = None,
    model_profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    mcp_profile: str | None = None,
    sandbox_profile: str | None = None,
    preset: str | None = None,
    game_engine_ref: str | None = None,
    dsh_ref: str | None = None,
    evaluate: bool = True,
    repeat: int = 1,
    runs_root: Path | None = None,
) -> dict[str, Any]:
    """Execute each Agent ``repeat`` times under identical Task conditions."""
    selected = tuple(dict.fromkeys(agent.strip() for agent in agents if agent.strip()))
    if not selected:
        raise ComparisonError("compare requires at least one Agent id")

    supplied = {
        "model_profile": model_profile,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "mcp_profile": mcp_profile,
        "sandbox_profile": sandbox_profile,
        "preset": preset,
        "game_engine_ref": game_engine_ref,
        "dsh_ref": dsh_ref,
    }
    # An explicit flag is a frozen input; an omitted one is left to the Task and
    # project defaults, and recording `None` as frozen would claim the design
    # pinned something it never mentioned.
    fixed = {key: str(value) for key, value in supplied.items() if value is not None}

    spec = ExperimentSpec(
        experiment_id=f"compare-{task_id}",
        task_id=task_id,
        vary={"agent": selected},
        fixed=fixed,
        repeats=repeat,
        description=f"{len(selected)} Agents on {task_id}",
    )
    # The runner emits `experiment_run_id`/`cells`/`attempts`. A comparison is
    # indexed by the Console from `comparisons/*/comparison.json` and read
    # through its own projection, so the payload keeps that shape and its own
    # directory: same numbers and the same extension point the reader already
    # walks. `experiment run` writes the richer artifact under `experiments/`.
    payload = run_experiment(
        repo_root,
        spec,
        config_path=config_path,
        evaluate=evaluate,
        runs_root=runs_root,
        artifact_dir="comparisons",
    )
    comparison = {
        "comparison_id": payload["experiment_run_id"],
        "created_at": payload["created_at"],
        "config_path": payload["config_path"],
        "invariant": payload["invariant"],
        "repeat": payload["definition"]["repeats"],
        "runs": [
            _as_comparison_run(attempt, agents=selected) for attempt in payload["attempts"]
        ],
        "agents": [
            {**cell, "agent": cell.get("selectors", {}).get("agent", "")}
            for cell in payload["cells"]
        ],
        "discrimination": payload["discrimination"],
        "output_dir": payload["output_dir"],
    }
    _write_comparison_artifact(Path(payload["output_dir"]), comparison)
    return comparison


def _write_comparison_artifact(output_dir: Path, payload: dict[str, Any]) -> None:
    """Persist ``comparison.json`` + ``comparison.md`` where the Console looks."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "comparison.md").write_text(
        render_comparison_markdown(payload), encoding="utf-8"
    )


def _as_comparison_run(attempt: dict[str, Any], *, agents: tuple[str, ...]) -> dict[str, Any]:
    """One attempt in the flat shape the Console's comparison reader expects.

    It iterates `runs` and reads `run_id`/`agent`/`status`/`evaluation`/`error`
    from each entry, so those keys must stay exactly where they were.
    """
    record = {
        key: attempt[key]
        for key in (
            "agent",
            "model",
            "run_id",
            "attempt",
            "status",
            "run_dir",
            "evaluation",
            "error",
        )
        if key in attempt
    }
    selectors = attempt.get("selectors") or {}
    if not record.get("agent"):
        record["agent"] = selectors.get("agent") or (agents[0] if agents else "")
    return record


def render_comparison_markdown(payload: dict[str, Any]) -> str:
    """Render a comparison with the same tables an experiment produces."""
    return render_experiment_markdown(
        {
            "experiment_run_id": payload.get("comparison_id", ""),
            "experiment_id": f"compare-{payload.get('invariant', {}).get('task_id', '')}",
            "created_at": payload.get("created_at", ""),
            "definition": {
                "task_id": payload.get("invariant", {}).get("task_id", ""),
                "vary": {"agent": [item.get("agent", "") for item in payload.get("agents", [])]},
                "fixed": payload.get("invariant", {}).get("fixed", {}),
                "repeats": payload.get("repeat", 1),
            },
            "cells": [
                {**cell, "label": cell.get("agent") or cell.get("label", "")}
                for cell in payload.get("agents", [])
            ],
            "attempts": [
                {**run, "cell": run.get("agent", "")} for run in payload.get("runs", [])
            ],
            "discrimination": payload.get("discrimination", {}),
            "output_dir": payload.get("output_dir", ""),
        }
    )
