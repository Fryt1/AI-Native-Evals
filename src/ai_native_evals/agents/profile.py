"""Declarative Agent profile loaded from ``profiles/agents/*.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
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
    #: The Agent's own version, when the profile names one. Recorded in the run
    #: manifest so a result can be traced to the exact build it came from.
    agent_version: str = ""

    @classmethod
    def from_mapping(cls, profile_id: str, value: Mapping[str, Any]) -> AgentProfile:
        """Validate and materialize a profile mapping."""
        if not profile_id.strip():
            raise AgentProfileError("agent profile id must be non-empty")
        adapter = _string(value, "adapter", "")
        if not adapter:
            raise AgentProfileError(f"agent profile {profile_id!r} requires adapter")
        # `agent_version` plus `image_repository` is the preferred spelling: the
        # tag is then derived from the version, so the two cannot drift. A bare
        # `image` is still accepted for profiles that name no version.
        agent_version = _string(value, "agent_version", "")
        image = _string(value, "image", "")
        if not image:
            repository = _string(value, "image_repository", "")
            if repository and agent_version:
                image = f"{repository}:{agent_version}"
            elif repository:
                raise AgentProfileError(
                    f"agent profile {profile_id!r} sets image_repository but no agent_version"
                )
        if not image:
            raise AgentProfileError(f"agent profile {profile_id!r} requires image")
        if agent_version and ":" in image and not image.endswith(f":{agent_version}"):
            raise AgentProfileError(
                f"agent profile {profile_id!r} image {image!r} does not end in its "
                f"declared agent_version {agent_version!r}"
            )
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
            agent_version=agent_version,
        )

    @property
    def trace_parser(self) -> str:
        """Which trace parser reads this profile's log.

        This used to be the bare `adapter` value, which conflated two unrelated
        jobs: selecting the in-process implementation and selecting a log
        parser. They are separate now, and the parser falls back to a
        pass-through so an unknown Agent still produces usable events rather
        than an empty Process phase.
        """
        declared = self.options.get("trace_parser")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return self.adapter or "generic"

    @property
    def prompt_delivery(self) -> str:
        """How the Task prompt reaches the Agent.

        Derived from what the profile declares, so a verification probe cannot
        disagree with a real run: both ask this property rather than each
        deciding for itself. An earlier probe passed the prompt in argv while
        every real run mounted it as a file, so any Agent reading the file was
        reported broken by the probe while working perfectly.
        """
        for item in self.command:
            if "${TASK_PROMPT}" in item or "TASK_PROMPT" in item:
                return "argv"
        if any("<" in item and "TASK_PROMPT" in item for item in self.command):
            return "stdin"
        return "file"

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
            "agent_version": self.agent_version,
            # Reported so a reader does not have to re-derive either, which is
            # how the two consumers of this profile drifted apart before.
            "trace_parser": self.trace_parser,
            "prompt_delivery": self.prompt_delivery,
        }


def load_agent_profile_file(path: Path) -> AgentProfile:
    """Load one Agent profile from a YAML file.

    The single place a profile file becomes an AgentProfile. `preflight` and the
    Agent check each used to read the YAML themselves and interpret the fields,
    so a change to how `image` resolves fixed one and silently missed the other.
    """
    import yaml

    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, Mapping):
        raise AgentProfileError(f"agent profile {path.name} is not a mapping")
    return AgentProfile.from_mapping(str(value.get("id") or path.stem), value)


def load_agent_profiles(root: Path) -> dict[str, AgentProfile]:
    """Load every profile under a directory, keyed by declared id.

    A malformed profile is skipped rather than raising: a directory holding one
    broken file should still let `doctor` report the others.
    """
    profiles: dict[str, AgentProfile] = {}
    if not root.is_dir():
        return profiles
    for path in sorted(root.glob("*.yaml")):
        try:
            profile = load_agent_profile_file(path)
        except (AgentProfileError, OSError, ValueError):
            continue
        profiles[profile.profile_id] = profile
    return profiles


def _string(value: Mapping[str, Any], key: str, default: str) -> str:
    child = value.get(key, default)
    if child is None:
        return default
    return str(child).strip()
