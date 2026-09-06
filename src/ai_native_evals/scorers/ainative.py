"""Inspect AI scorers for AI-Native domain results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState

from ..contracts import Verdict


def _payload_from_state(
    state: TaskState, verdict_filename: str
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Read an inline or file-backed verifier payload from the task state."""
    inline = state.store.get("ai_native_verdict")
    if inline is not None:
        if not isinstance(inline, Mapping):
            return None, "inline ai_native_verdict must be an object"
        return inline, None

    configured_path = state.metadata.get("verdict_path")
    if configured_path is None:
        run_dir = state.metadata.get("run_dir")
        if run_dir is None:
            return None, "no verdict_path or run_dir was provided"
        configured_path = str(Path(str(run_dir)) / verdict_filename)

    path = Path(str(configured_path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"verdict file does not exist: {path}"
    except OSError as exc:
        return None, f"could not read verdict file {path}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"verdict file is not valid JSON: {exc}"

    if not isinstance(payload, Mapping):
        return None, "verdict file must contain a JSON object"
    return payload, None


@scorer(metrics=[accuracy()], name="ai_native_engine_scorer")
def ai_native_engine_scorer(verdict_filename: str = "ainative-verdict.json") -> Scorer:
    """Score a sample from an AI-Native Game Engine verifier payload.

    The first implementation accepts either a JSON verdict file in the run
    directory or an inline payload in ``TaskState.store``. The inline path is
    intentionally useful for the smoke task and unit tests; real Agent runs
    should use a file produced by the domain verifier.
    """

    async def score(state: TaskState, target: Target) -> Score:
        del target
        payload, error = _payload_from_state(state, verdict_filename)
        if error is not None or payload is None:
            return Score(
                value=False,
                answer="scoring_failed",
                explanation=error or "missing verifier payload",
                reason="scoring_failed",
            )

        try:
            verdict = Verdict.from_mapping(payload)
        except ValueError as exc:
            return Score(
                value=False,
                answer="invalid_verdict",
                explanation=str(exc),
                reason="scoring_failed",
            )

        return Score(
            value=verdict.passed,
            answer=verdict.status,
            explanation=(
                f"status={verdict.status}; score={verdict.score:.3f}; "
                f"evidence_complete={verdict.evidence_complete}"
            ),
            metadata=verdict.to_dict(),
        )

    return score
