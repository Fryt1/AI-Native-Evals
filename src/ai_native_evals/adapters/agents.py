"""External Agent adapters will live at this seam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentLaunchSpec:
    """Stable launch inputs shared by Codex and DSH adapters."""

    agent_id: str
    run_dir: Path
    task_text: str
    timeout_seconds: int = 1800


class AgentAdapterNotImplemented(RuntimeError):
    """Raised by the initial skeleton until an Agent adapter is wired."""
