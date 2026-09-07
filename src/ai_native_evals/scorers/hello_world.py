"""Hello-world Scorer: verify one evidence file has the exact expected text.

This is the canonical example of how a task-specific Scorer is written in this
suite:

- it never asks the Agent whether it succeeded; it reads the file from disk
- expectations come from the task (prompt + scorer must agree), not from prose
- every failure mode is classified with a machine-readable ``answer`` so the
  digest/summary can show *why* a run failed (missing vs. mismatch vs. tool
  error) instead of a bare boolean
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.model import ChatMessageSystem
from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState


@scorer(metrics=[accuracy()], name="hello_world_scorer")
def hello_world_scorer(
    *,
    relative_path: str = "evidence/hello.txt",
    expected_text: str = "ai-native-codex-ok",
) -> Scorer:
    """Check one file in the Agent evidence dir without trusting Agent prose.

    Args:
        relative_path: Path relative to the run directory (defaults to the
            evidence/hello.txt location the codex-file-smoke prompt uses).
        expected_text: Exact text the file must contain.
    """

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
        state.messages.append(
            ChatMessageSystem(
                content=f"File changed: {path.name}",
                source="generate",
                metadata={"eval_event": "file_changed", "path": str(path)},
            )
        )
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
                "relative_path": relative_path,
                "expected": expected_text,
                "expected_length": len(expected_text),
                "actual_length": len(actual_text),
                "agent_run": state.store.get("agent_run"),
                "codex_event_count": state.store.get("codex_event_count"),
            },
        )

    return score
