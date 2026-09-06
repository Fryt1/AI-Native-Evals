"""Environment seams reserved for local and remote DCC workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Description of one isolated evaluation workspace."""

    run_dir: Path
    seed_dir: Path | None = None
    worker_kind: str = "local-windows-dcc"


class SandboxNotImplemented(RuntimeError):
    """Raised until a concrete Blender/UE5 worker is selected."""


def prepare_workspace(spec: WorkspaceSpec) -> Path:
    """Reserve the sandbox seam without silently touching a DCC project."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    return spec.run_dir
