"""Inspect scorers for deterministic workspace state."""

from __future__ import annotations

from pathlib import Path

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState


@scorer(metrics=[accuracy()], name="workspace_file_scorer")
def workspace_file_scorer(relative_path: str, expected_text: str) -> Scorer:
    """Check one file in the Agent run directory without trusting Agent prose."""

    async def score(state: TaskState, target: Target) -> Score:
        del target
        run_dir = state.store.get("run_dir")
        if not isinstance(run_dir, str):
            return Score(
                value=False,
                answer="scoring_failed",
                explanation="solver did not publish a run_dir",
                reason="scoring_failed",
            )

        path = Path(run_dir) / relative_path
        try:
            actual_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Score(
                value=False,
                answer="missing",
                explanation=f"expected file does not exist: {path}",
                reason="wrong_outcome",
            )
        except OSError as exc:
            return Score(
                value=False,
                answer="scoring_failed",
                explanation=f"could not read {path}: {exc}",
                reason="scoring_failed",
            )

        passed = actual_text == expected_text
        return Score(
            value=passed,
            answer="pass" if passed else "content_mismatch",
            explanation=(
                f"{path.name} matches the expected content"
                if passed
                else f"{path.name} content did not match the expected value"
            ),
            metadata={
                "path": str(path),
                "expected_length": len(expected_text),
                "actual_length": len(actual_text),
                "agent_run": state.store.get("agent_run"),
            },
        )

    return score
