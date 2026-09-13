"""Task-declared resource snapshots and provider implementations."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ResourceKind = Literal["repository", "directory", "fixture", "host_service"]


class ResourceError(ValueError):
    """Raised when a task resource cannot be materialized."""


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One resource explicitly requested by a Task bundle."""

    resource_id: str
    kind: ResourceKind
    source: Path | None = None
    mount: str | None = None
    ref: str | None = None
    read_only: bool = False
    required: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        repo_root: Path,
        source_roots: Mapping[str, Any],
        index: int,
    ) -> ResourceSpec:
        if not isinstance(value, Mapping):
            raise ResourceError(f"resources[{index}] must be a mapping")
        resource_id = _required_string(value, "id", f"resources[{index}]")
        kind = _required_string(value, "kind", f"resource {resource_id!r}")
        if kind not in {"repository", "directory", "fixture", "host_service"}:
            raise ResourceError(f"resource {resource_id!r} has unsupported kind: {kind}")
        raw_source = value.get("source")
        source = _resolve_source(raw_source, repo_root=repo_root, source_roots=source_roots)
        if kind != "host_service" and source is None:
            raise ResourceError(f"resource {resource_id!r} requires source")
        mount = value.get("mount", resource_id)
        if mount is not None and (not isinstance(mount, str) or not mount.strip()):
            raise ResourceError(f"resource {resource_id!r} mount must be a non-empty string")
        if mount is not None:
            mount_path = Path(str(mount))
            if mount_path.is_absolute() or ".." in mount_path.parts:
                raise ResourceError(
                    f"resource {resource_id!r} mount must stay inside the workspace"
                )
        ref = value.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise ResourceError(f"resource {resource_id!r} ref must be a string")
        read_only = value.get("read_only", False)
        required = value.get("required", True)
        if not isinstance(read_only, bool) or not isinstance(required, bool):
            raise ResourceError(f"resource {resource_id!r} read_only/required must be boolean")
        options = value.get("options", {})
        if not isinstance(options, Mapping):
            raise ResourceError(f"resource {resource_id!r} options must be a mapping")
        return cls(
            resource_id=resource_id,
            kind=kind,  # type: ignore[arg-type]
            source=source,
            mount=str(mount) if mount is not None else None,
            ref=ref,
            read_only=read_only,
            required=required,
            options=dict(options),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.resource_id,
            "kind": self.kind,
            "source": str(self.source) if self.source else None,
            "mount": self.mount,
            "ref": self.ref,
            "read_only": self.read_only,
            "required": self.required,
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Durable result of preparing one resource for a run."""

    resource_id: str
    kind: ResourceKind
    source: str | None
    destination: str | None
    snapshot: dict[str, Any]
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "source": self.source,
            "destination": self.destination,
            "snapshot": self.snapshot,
            "required": self.required,
        }


class ResourceProvider(Protocol):
    """Seam for turning a declared resource into run-local evidence."""

    def prepare(self, spec: ResourceSpec, destination: Path) -> ResourceSnapshot:
        """Prepare one resource at the destination without mutating its source."""
        ...


class GitSnapshotProvider:
    """Snapshot Git refs or working trees into the run workspace."""

    def prepare(self, spec: ResourceSpec, destination: Path) -> ResourceSnapshot:
        if spec.source is None:
            raise ResourceError(f"resource {spec.resource_id!r} has no source")
        from ..runs.snapshots import snapshot_repository

        metadata = snapshot_repository(spec.source, destination, spec.ref)
        return ResourceSnapshot(
            resource_id=spec.resource_id,
            kind=spec.kind,
            source=str(spec.source),
            destination=str(destination),
            snapshot=metadata,
            required=spec.required,
        )


class DirectorySnapshotProvider(GitSnapshotProvider):
    """Directory/fixture provider; the shared snapshot code also handles non-Git paths."""


class HostServiceProvider:
    """Record a host service without copying it into the Agent workspace."""

    def prepare(self, spec: ResourceSpec, destination: Path) -> ResourceSnapshot:
        del destination
        return ResourceSnapshot(
            resource_id=spec.resource_id,
            kind=spec.kind,
            source=str(spec.source) if spec.source else None,
            destination=None,
            snapshot={"mode": "host_service", **spec.options},
            required=spec.required,
        )


_PROVIDERS: dict[str, ResourceProvider] = {
    "repository": GitSnapshotProvider(),
    "directory": DirectorySnapshotProvider(),
    "fixture": DirectorySnapshotProvider(),
    "host_service": HostServiceProvider(),
}


def provider_for(kind: ResourceKind) -> ResourceProvider:
    """Return the provider for a validated resource kind."""
    try:
        return _PROVIDERS[kind]
    except KeyError as exc:
        raise ResourceError(f"no resource provider for {kind!r}") from exc


def prepare_resources(
    specs: tuple[ResourceSpec, ...],
    workspace_dir: Path,
) -> tuple[dict[str, ResourceSnapshot], dict[str, Path]]:
    """Prepare all task-declared resources and return snapshots plus host paths."""
    snapshots: dict[str, ResourceSnapshot] = {}
    paths: dict[str, Path] = {}
    for spec in specs:
        destination = (workspace_dir / (spec.mount or spec.resource_id)).resolve()
        try:
            destination.relative_to(workspace_dir.resolve())
        except ValueError as exc:
            raise ResourceError(
                f"resource {spec.resource_id!r} destination escapes workspace"
            ) from exc
        if spec.kind == "host_service":
            snapshot = provider_for(spec.kind).prepare(spec, destination)
        else:
            if spec.source is None or not spec.source.exists():
                if spec.required:
                    raise FileNotFoundError(
                        f"resource {spec.resource_id!r} source does not exist: {spec.source}"
                    )
                continue
            if destination.exists():
                shutil.rmtree(destination)
            snapshot = provider_for(spec.kind).prepare(spec, destination)
            paths[spec.resource_id] = destination
        snapshots[spec.resource_id] = snapshot
    return snapshots, paths


def _resolve_source(
    raw_source: Any,
    *,
    repo_root: Path,
    source_roots: Mapping[str, Any],
    refresh_remotes: bool = False,
) -> Path | None:
    if raw_source is None:
        return None
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise ResourceError("resource source must be a non-empty string")
    value = raw_source.strip()
    if value.startswith("${paths.") and value.endswith("}"):
        value = value[len("${paths.") : -1]
    if value.startswith("paths."):
        value = value.removeprefix("paths.")

    ref: str | None = None
    if value in source_roots:
        mapped = source_roots[value]
        if isinstance(mapped, Mapping):
            # An id may carry its own default ref, so a Task does not have to
            # repeat it: {url: ..., ref: v1.2.0}
            ref = _optional_str(mapped.get("ref"))
            url_value = mapped.get("url") or mapped.get("path")
            value = str(url_value).strip() if url_value is not None else ""
        else:
            value = str(mapped).strip()
    elif _looks_like_source_id(value) and value not in source_roots:
        # A bare name that maps to nothing would otherwise be read as a relative
        # directory and resolved to a path that silently does not exist. The
        # tracked config ships with `source_roots: {}`, so this is the first
        # thing a fresh clone hits -- say what to do rather than reporting a
        # missing directory somewhere unrelated.
        configured = ", ".join(sorted(source_roots)) or "none"
        raise ResourceError(
            f"resource source {value!r} is not configured on this machine; "
            f"add it under paths.source_roots in config/eval.local.yaml "
            f"(configured: {configured})"
        )

    if _is_remote_source(value):
        # A remote id is materialised into the local checkout cache and then
        # treated exactly like a local path, so no caller downstream has to
        # know where the code came from.
        from ..runs.remotes import resolve_remote

        checkout = resolve_remote(
            value,
            repo_root=repo_root,
            ref=ref,
            refresh=refresh_remotes or _refresh_requested(),
        )
        return checkout.path

    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_remote_source(value: str) -> bool:
    """Whether a source string names a Git remote.

    Imported lazily: ``runs`` imports this module, so a module-level import of
    ``runs.remotes`` here would close a cycle.
    """
    from ..runs.remotes import looks_like_remote

    return looks_like_remote(value)


def _refresh_requested() -> bool:
    """Whether this process was asked to move remote checkouts forward.

    Read from the environment rather than threaded through every signature:
    resolution is called from the CLI, the Console, preflight and the compare
    path, and a single flag is the whole contract.
    """
    value = os.environ.get("AI_NATIVE_EVALS_REFRESH_SOURCES", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _looks_like_source_id(value: str) -> bool:
    """Whether a source string names a configured id rather than a path.

    Ids are single bare identifiers (`my_project`); paths carry a separator, a
    drive letter, or a dot-prefixed segment. Only the former can be mistaken for
    an unconfigured id.
    """
    if not value or value in {".", ".."}:
        return False
    if any(separator in value for separator in ("/", "\\")):
        return False
    if ":" in value or value.startswith("."):
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _required_string(value: Mapping[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str) or not child.strip():
        raise ResourceError(f"{context} requires a non-empty string {key!r}")
    return child.strip()



