"""Inspect Solver that drives the external Codex CLI."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from inspect_ai.solver import Generate, Solver, TaskState, solver

from ai_native_evals.adapters.codex import CodexAdapter, CodexConfig
from ai_native_evals.contracts import AgentLaunchSpec
from ai_native_evals.sandboxes import WorkspaceSpec, prepare_workspace


@solver
def codex_agent(
    timeout_seconds: int = 1800,
    model: str | None = None,
    executable: str = "codex",
    run_root_override: str | Path | None = None,
) -> Solver:
    """Run one external Codex process for the current Inspect sample."""
    adapter = CodexAdapter(CodexConfig(executable=executable))

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        run_root_value = (
            run_root_override
            if run_root_override is not None
            else state.metadata.get(
                "run_root", os.environ.get("AI_NATIVE_EVALS_RUN_ROOT", "runs")
            )
        )
        run_root = Path(str(run_root_value)).expanduser().resolve()
        sample_id = _safe_component(str(state.sample_id))
        run_id = f"codex-{sample_id}-{uuid4().hex[:10]}"
        run_dir = prepare_workspace(WorkspaceSpec(run_dir=run_root / run_id))
        spec = AgentLaunchSpec(
            agent_id="codex",
            task_id=str(state.sample_id),
            run_dir=run_dir,
            task_text=str(state.input),
            timeout_seconds=timeout_seconds,
            model=model,
        )
        result = await adapter.run(spec)
        state.store.set("agent_run", result.to_dict())
        state.store.set("run_dir", str(run_dir))
        return state

    return solve


def _safe_component(value: str) -> str:
    """Convert a sample id into a safe single path component."""
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    return normalized or "sample"
