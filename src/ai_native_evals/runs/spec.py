"""Resolved configuration and immutable per-run metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..plans import TestPlan


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One resolved evaluation run before an Agent is started."""

    run_id: str
    task_id: str
    task_prompt: str
    agent: str
    agent_image: str
    model_profile: str
    model: str
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
    snapshot_mode: str
    game_engine_root: Path
    dsh_root: Path
    runs_root: Path
    run_dir: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe resolved run metadata."""
        payload = asdict(self)
        for key in ("game_engine_root", "dsh_root", "runs_root", "run_dir"):
            payload[key] = str(payload[key])
        # Keep the plan's explicit normalized representation rather than the
        # dataclass field order produced by ``asdict``.
        payload["test_plan"] = self.test_plan.to_dict()
        return payload
