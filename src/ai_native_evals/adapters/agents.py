"""Common external Agent adapter contracts."""

from ..agents.base import AgentAdapter
from ..contracts import AgentAdapterNotImplemented, AgentLaunchSpec, AgentRunResult, AgentRunStatus

__all__ = [
    "AgentAdapter",
    "AgentAdapterNotImplemented",
    "AgentLaunchSpec",
    "AgentRunResult",
    "AgentRunStatus",
]
