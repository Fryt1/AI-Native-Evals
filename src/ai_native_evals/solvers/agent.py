"""Generic Inspect Solver that selects an Agent profile at runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from inspect_ai.solver import Generate, Solver, TaskState, solver

from ai_native_evals.adapters.inspect_events import project_normalized_events
from ai_native_evals.agents import AgentProfile, create_adapter
from ai_native_evals.contracts import AgentLaunchSpec
from ai_native_evals.sandboxes import WorkspaceSpec, prepare_workspace


@solver
def agent_solver(
    agent_id: str = "codex",
    timeout_seconds: int = 1800,
    model: str | None = None,
    run_root_override: str | Path | None = None,
    profile: Mapping[str, Any] | None = None,
) -> Solver:
    """Run any registered Agent adapter for the current Inspect sample."""

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
        run_id = f"{agent_id}-{sample_id}-{uuid4().hex[:10]}"
        run_dir = prepare_workspace(WorkspaceSpec(run_dir=run_root / run_id))
        effective = dict(profile or state.metadata.get("agent_profile", {}))
        if not effective:
            effective = {
                "id": agent_id,
                "adapter": "dsh-acp" if agent_id == "dsh" else agent_id,
                "image": "local",
                "protocol": "acp" if agent_id == "dsh" else "responses",
            }
        agent_profile = AgentProfile.from_mapping(agent_id, effective)
        spec = AgentLaunchSpec(
            agent_id=agent_id,
            task_id=str(state.sample_id),
            run_dir=run_dir,
            task_text=str(state.input),
            timeout_seconds=timeout_seconds,
            model=model,
            protocol=agent_profile.protocol,
            working_directory=agent_profile.workdir,
            system_prompt=agent_profile.system_prompt,
            workspace_dir=run_dir,
            options=agent_profile.options,
        )
        result = await create_adapter(agent_profile).run(spec)
        state.store.set("agent_run", result.to_dict())
        state.store.set("agent_profile", agent_profile.to_dict())
        state.store.set("run_dir", str(run_dir))
        normalized_path = result.normalized_events_path
        if normalized_path and normalized_path.is_file():
            projected = project_normalized_events(normalized_path)
            state.store.set("agent_event_count", len(projected))
            state.messages.extend(projected)
        return state

    return solve


def _safe_component(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    return normalized or "sample"
