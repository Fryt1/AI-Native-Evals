"""Inspect Solvers that launch provider-specific Agents."""

from .agent import agent_solver
from .codex import codex_agent
from .mock import mock_agent


def dsh_agent(**kwargs):
    """Convenience wrapper selecting the standard DSH ACP adapter."""
    return agent_solver(agent_id="dsh", **kwargs)


__all__ = ["agent_solver", "codex_agent", "dsh_agent", "mock_agent"]
