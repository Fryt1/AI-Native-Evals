"""Agent adapter interface used by the evaluation harness."""

from __future__ import annotations

from typing import Protocol

from ..contracts import AgentLaunchSpec, AgentRunResult


class AgentAdapter(Protocol):
    """Deep seam between the harness and an external Agent runtime.

    An adapter owns provider-specific process/protocol details. The harness only
    supplies a stable launch spec and consumes a durable run result.
    """

    adapter_id: str

    async def run(self, spec: AgentLaunchSpec) -> AgentRunResult:
        """Run one Agent and persist raw plus normalized execution evidence."""
        ...
