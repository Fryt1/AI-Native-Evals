"""Resolved configuration and immutable per-run metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agents import AgentProfile
from ..plans import TestPlan
from ..resources import ResourceSpec


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One resolved evaluation run before an Agent is started.

    The run contains a task bundle plus an Agent profile, but neither side
    depends on the other. The workspace is materialized later from the task's
    explicit ``resource_specs``.
    """

    run_id: str
    task_id: str
    task_prompt: str
    agent: str
    agent_image: str
    model_profile: str
    model: str
    model_provider: str | None
    protocol: str
    reasoning_effort: str | None
    mcp_profile: str
    mcp_host: str
    mcp_port: int
    mcp_blender: bool
    mcp_ue5: bool
    mcp_servers: dict[str, Any]
    verify: dict[str, Any]
    test_plan: TestPlan
    sandbox: dict[str, Any]
    sandbox_profile: str
    snapshot_mode: str
    game_engine_root: Path | None
    dsh_root: Path | None
    runs_root: Path
    run_dir: Path
    agent_profile: AgentProfile = field(
        default_factory=lambda: AgentProfile("unknown", "codex", "unknown")
    )
    evaluator_agent: str = "codex"
    evaluator_agent_profile: AgentProfile = field(
        default_factory=lambda: AgentProfile("codex", "codex", "unknown")
    )
    resource_specs: tuple[ResourceSpec, ...] = ()
    task_bundle: dict[str, Any] = field(default_factory=dict)
    preset: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe resolved run metadata."""
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_prompt": self.task_prompt,
            "agent": self.agent,
            "agent_image": self.agent_image,
            "agent_profile": self.agent_profile.to_dict(),
            "evaluator_agent": self.evaluator_agent,
            "evaluator_agent_profile": self.evaluator_agent_profile.to_dict(),
            "model_profile": self.model_profile,
            "model": self.model,
            "model_provider": self.model_provider,
            "protocol": self.protocol,
            "reasoning_effort": self.reasoning_effort,
            "mcp_profile": self.mcp_profile,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_blender": self.mcp_blender,
            "mcp_ue5": self.mcp_ue5,
            "mcp_servers": self.mcp_servers,
            "verify": self.verify,
            "test_plan": self.test_plan.to_dict(),
            "sandbox": self.sandbox,
            "sandbox_profile": self.sandbox_profile,
            "snapshot_mode": self.snapshot_mode,
            "game_engine_root": str(self.game_engine_root) if self.game_engine_root else None,
            "dsh_root": str(self.dsh_root) if self.dsh_root else None,
            "runs_root": str(self.runs_root),
            "run_dir": str(self.run_dir),
            "resource_specs": [spec.to_dict() for spec in self.resource_specs],
            "task_bundle": self.task_bundle,
            "preset": self.preset,
            "created_at": self.created_at,
        }
        return payload
