"""Compatibility wrapper for the generic Inspect Agent Solver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from inspect_ai.solver import Solver, solver

from .agent import agent_solver


@solver
def codex_agent(
    timeout_seconds: int = 1800,
    model: str | None = None,
    executable: str = "codex",
    run_root_override: str | Path | None = None,
) -> Solver:
    """Run Codex through the common AgentAdapter seam.

    ``executable`` remains a compatibility option for local Inspect smoke
    tests. Docker runs select the executable from the Codex profile instead.
    """
    profile: dict[str, Any] = {
        "id": "codex",
        "adapter": "codex",
        "image": "local",
        "protocol": "responses",
        "options": {"executable": executable},
    }
    return agent_solver(
        agent_id="codex",
        timeout_seconds=timeout_seconds,
        model=model,
        run_root_override=run_root_override,
        profile=profile,
    )
