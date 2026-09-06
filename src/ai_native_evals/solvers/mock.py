"""Solvers used only for deterministic smoke coverage."""

from __future__ import annotations

from typing import Literal

from inspect_ai.solver import Generate, Solver, TaskState, solver

from ..contracts import Verdict


@solver
def mock_agent(status: Literal["succeeded", "degraded", "failed"] = "succeeded") -> Solver:
    """Write a deterministic verifier payload without calling a model."""
    verdict = Verdict(
        status=status,
        score=1.0 if status in {"succeeded", "degraded"} else 0.0,
        passed=status in {"succeeded", "degraded"},
        evidence_complete=status in {"succeeded", "degraded"},
    ).to_dict()

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        state.store.set("ai_native_verdict", verdict)
        return state

    return solve
