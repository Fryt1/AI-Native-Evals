"""Declarative Agent profile loaded from ``profiles/agents/*.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class AgentProfileError(ValueError):
    """Raised when an Agent profile is malformed."""


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Provider-neutral launch metadata for one Agent implementation."""

    profile_id: str
    adapter: str
    image: str
    protocol: str = "responses"
    workdir: str = "/workspace"
    entrypoint: str | None = None
    system_prompt: str | None = None
    command: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    writable_paths: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, profile_id: str, value: Mapping[str, Any]) -> AgentProfile:
        """Validate and materialize a profile mapping."""
        if not profile_id.strip():
            raise AgentProfileError("agent profile id must be non-empty")
        adapter = _string(value, "adapter", "")
        if not adapter:
            raise AgentProfileError(f"agent profile {profile_id!r} requires adapter")
        image = _string(value, "image", "")
        if not image:
            raise AgentProfileError(f"agent profile {profile_id!r} requires image")
        entrypoint = value.get("entrypoint")
        if entrypoint is not None and (not isinstance(entrypoint, str) or not entrypoint.strip()):
            raise AgentProfileError(f"agent profile {profile_id!r} entrypoint must be a string")
        system_prompt = value.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise AgentProfileError(f"agent profile {profile_id!r} system_prompt must be a string")
        command = value.get("command", ())
        if isinstance(command, str):
            command = (command,)
        if not isinstance(command, (list, tuple)) or not all(
            isinstance(item, str) and item for item in command
        ):
            raise AgentProfileError(f"agent profile {profile_id!r} command must be a list")
        environment = value.get("environment", value.get("env", {}))
        if not isinstance(environment, Mapping):
            raise AgentProfileError(f"agent profile {profile_id!r} environment must be a mapping")
        writable_paths = value.get("writable_paths", ())
        capabilities = value.get("capabilities", ())
        for field_name, field_value in (
            ("writable_paths", writable_paths),
            ("capabilities", capabilities),
        ):
            if not isinstance(field_value, (list, tuple)) or not all(
                isinstance(item, str) and item for item in field_value
            ):
                raise AgentProfileError(
                    f"agent profile {profile_id!r} {field_name} must be a list of strings"
                )
        options = value.get("options", {})
        if not isinstance(options, Mapping):
            raise AgentProfileError(f"agent profile {profile_id!r} options must be a mapping")
        return cls(
            profile_id=profile_id,
            adapter=adapter,
            image=image,
            protocol=_string(value, "protocol", "responses"),
            workdir=_string(value, "workdir", "/workspace"),
            entrypoint=str(entrypoint).strip() if entrypoint is not None else None,
            system_prompt=str(system_prompt) if system_prompt is not None else None,
            command=tuple(command),
            environment={str(key): str(item) for key, item in environment.items()},
            writable_paths=tuple(writable_paths),
            capabilities=tuple(capabilities),
            options=dict(options),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe immutable profile snapshot."""
        return {
            "id": self.profile_id,
            "adapter": self.adapter,
            "image": self.image,
            "protocol": self.protocol,
            "workdir": self.workdir,
            "entrypoint": self.entrypoint,
            "system_prompt": self.system_prompt,
            "command": list(self.command),
            "environment": dict(self.environment),
            "writable_paths": list(self.writable_paths),
            "capabilities": list(self.capabilities),
            "options": dict(self.options),
        }


def _string(value: Mapping[str, Any], key: str, default: str) -> str:
    child = value.get(key, default)
    if child is None:
        return default
    return str(child).strip()
