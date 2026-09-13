"""Declarative Agent profile loaded from ``profiles/agents/*.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
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
    options: dict[str, Any] = field(default_factory=dict)
    #: The Agent's own version, when the profile names one. Recorded in the run
    #: manifest so a result can be traced to the exact build it came from.
    agent_version: str = ""
    #: Files placed where the Agent's own startup can find them, written as
    #: `source_id` or `source_id@destination`. The framework delivers them and
    #: says nothing about what they mean; the Agent reads that directory and
    #: composes them its own way. An earlier version of this field was called
    #: `plugins`, which put one Agent's composition mechanism into the
    #: framework's vocabulary and could not describe an Agent that composes
    #: differently -- or one that has no such mechanism at all.
    attach: tuple[str, ...] = ()
    #: How this Agent's image is produced, when the framework builds it.
    #: Declared here so the build tool needs no list of Agents: it reads the
    #: profile and does what the profile says. `kind` selects the recipe --
    #: `npm` installs a published package, `source` builds a checkout identified
    #: by commit, `prebuilt` means the image already exists.
    build: dict[str, Any] = field(default_factory=dict)
    #: How this Agent's log becomes normalized events: `{parser: codex}`. Its own
    #: field rather than a derivation, because reading a log and selecting an
    #: implementation are different questions.
    trace: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, profile_id: str, value: Mapping[str, Any]) -> AgentProfile:
        """Validate and materialize a profile mapping.

        Only `image` is genuinely required. `adapter` is optional because the
        Docker path -- the one `run execute` and the Console use -- starts a
        container from the profile's `entrypoint` and `command` and never
        consults it; it selects the in-process Inspect implementation, which
        most command-line Agents do not use.
        """
        if not profile_id.strip():
            raise AgentProfileError("agent profile id must be non-empty")
        adapter = _string(value, "adapter", "") or "generic"
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
        attach = value.get("attach", ())
        for field_name, field_value in (
            ("writable_paths", writable_paths),
            ("attach", attach),
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
        build = value.get("build", {})
        if not isinstance(build, Mapping):
            raise AgentProfileError(f"agent profile {profile_id!r} build must be a mapping")
        build_kind = str(build.get("kind") or "").strip()
        trace = value.get("trace", {})
        if not isinstance(trace, Mapping):
            raise AgentProfileError(f"agent profile {profile_id!r} trace must be a mapping")
        if build_kind and build_kind not in {"npm", "source", "prebuilt"}:
            raise AgentProfileError(
                f"agent profile {profile_id!r} build.kind must be npm, source or prebuilt"
            )
        needs_dockerfile = build_kind and build_kind != "prebuilt"
        if needs_dockerfile and not str(build.get("dockerfile") or "").strip():
            raise AgentProfileError(
                f"agent profile {profile_id!r} build of kind {build_kind!r} needs a dockerfile"
            )
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
            options=dict(options),
            agent_version=agent_version,
            attach=tuple(attach),
            build={str(key): item for key, item in build.items()},
            trace={str(key): item for key, item in trace.items()},
        )

    @property
    def trace_parser(self) -> str:
        """Which parser turns this Agent's log into normalized events.

        Its own field rather than a derivation of `adapter`. The two answer
        different questions -- `adapter` selects the in-process Inspect
        implementation, this selects a log reader -- and an Agent may change one
        without the other. Falls back to `generic`, which emits `provider_event`
        for anything it does not recognize, so an unfamiliar Agent still
        produces a usable Process phase rather than an empty one.
        """
        declared = self.trace.get("parser") if isinstance(self.trace, Mapping) else None
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return "generic"

    @property
    def image_repository(self) -> str:
        """The image name without its tag, or ``""`` when the image is untagged.

        Kept so a profile can be re-pointed at another version: the repository
        stays, only the tag moves.
        """
        name, _, tag = self.image.rpartition(":")
        # A colon inside a registry host (`registry:5000/agent`) is not a tag.
        if not name or "/" in tag:
            return ""
        return name

    def with_version(self, version: str) -> AgentProfile:
        """This profile, pinned to another version of the same Agent.

        Used to run one Agent across versions. The image is derived from the
        repository, so the tag cannot disagree with the version it claims.
        """
        version = version.strip()
        if not version:
            raise AgentProfileError("version override must be non-empty")
        repository = self.image_repository
        if not repository:
            raise AgentProfileError(
                f"agent profile {self.profile_id!r} image {self.image!r} has no "
                "repository to pin a version against"
            )
        return replace(self, agent_version=version, image=f"{repository}:{version}")

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
            "options": dict(self.options),
            "agent_version": self.agent_version,
            "attach": list(self.attach),
            "build": dict(self.build),
            "trace": dict(self.trace),
            # Reported so a reader does not re-derive it; deriving the parser
            # separately in each consumer is how two of them drifted apart.
            "trace_parser": self.trace_parser,
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
