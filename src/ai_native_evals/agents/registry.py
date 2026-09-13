"""Registry for concrete Agent adapters."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points

from .base import AgentAdapter
from .profile import AgentProfile

AdapterFactory = Callable[[AgentProfile], AgentAdapter]
_FACTORIES: dict[str, AdapterFactory] = {}


class AgentRegistryError(ValueError):
    """Raised when an Agent adapter cannot be resolved."""


def register_adapter(name: str, factory: AdapterFactory) -> None:
    """Register one adapter factory under a stable profile adapter id."""
    if not name or name in _FACTORIES:
        raise AgentRegistryError(f"adapter id is empty or already registered: {name!r}")
    _FACTORIES[name] = factory


def available_adapters() -> tuple[str, ...]:
    """Return registered adapter ids, loading built-ins on first use."""
    _load_builtins()
    return tuple(sorted(_FACTORIES))


def create_adapter(profile: AgentProfile) -> AgentAdapter:
    """Create the concrete adapter selected by a profile.

    Raises for an adapter id nobody registered. Only the in-process Inspect path
    calls this; the Docker path starts a container from the profile's
    `entrypoint` and `command` and never consults `adapter`, so a profile that
    exists only to run in Docker may leave it unset.
    """
    _load_builtins()
    factory = _FACTORIES.get(profile.adapter)
    if factory is None:
        raise AgentRegistryError(
            f"unknown Agent adapter {profile.adapter!r}; available={available_adapters()}. "
            "The Docker path does not use this field; it is required only for the "
            "in-process Inspect path."
        )
    return factory(profile)


def _load_builtins() -> None:
    if _FACTORIES:
        return
    from ..adapters.codex import CodexAdapter, CodexConfig
    from ..adapters.dsh_acp import DshAcpAdapter, DshConfig

    _FACTORIES.update(
        {
            "codex": lambda profile: CodexAdapter(CodexConfig.from_profile(profile)),
            "dsh-acp": lambda profile: DshAcpAdapter(DshConfig.from_profile(profile)),
        }
    )
    for plugin in entry_points(group="ai_native_evals.agents"):
        if plugin.name in _FACTORIES:
            continue
        loaded = plugin.load()
        if not callable(loaded):
            raise AgentRegistryError(f"Agent plugin {plugin.name!r} is not callable")
        _FACTORIES[plugin.name] = loaded
