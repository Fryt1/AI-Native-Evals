"""Manual run lifecycle: prepare, status, cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .resolver import resolve_run
from .snapshots import snapshot_repository
from .spec import RunSpec


class RunLifecycleError(RuntimeError):
    """Raised for invalid run lifecycle operations."""


def prepare_run(repo_root: Path, spec: RunSpec, *, game_engine_ref: str | None = None, dsh_ref: str | None = None) -> Path:
    """Create a run workspace and write its resolved manifest."""
    run_dir = spec.run_dir.resolve()
    if run_dir.exists():
        raise RunLifecycleError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    project_dir = run_dir / "project" / "game-engine"
    dsh_dir = run_dir / "project" / "ai-native-dsh"
    for child in (run_dir / "workspace", run_dir / "artifacts", run_dir / "evidence", run_dir / "trace"):
        child.mkdir(parents=True)

    game_snapshot = snapshot_repository(spec.game_engine_root, project_dir, game_engine_ref)
    dsh_snapshot = None
    if spec.dsh_root.is_dir():
        dsh_snapshot = snapshot_repository(spec.dsh_root, dsh_dir, dsh_ref)

    manifest: dict[str, Any] = {
        "status": "prepared",
        "run": spec.to_dict(),
        "snapshots": {
            "game_engine": game_snapshot,
            "ai_native_dsh": dsh_snapshot,
        },
        "paths": {
            "project": str(project_dir),
            "dsh": str(dsh_dir),
            "workspace": str(run_dir / "workspace"),
            "artifacts": str(run_dir / "artifacts"),
            "evidence": str(run_dir / "evidence"),
            "trace": str(run_dir / "trace"),
        },
    }
    _write_manifest(run_dir, manifest)
    return run_dir


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a run manifest from a directory or manifest path."""
    manifest_path = path / "run-manifest.json" if path.is_dir() else path
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def set_status(run_dir: Path, status: str) -> dict[str, Any]:
    """Update a run status without changing resolved configuration."""
    manifest = load_manifest(run_dir)
    manifest["status"] = status
    _write_manifest(run_dir, manifest)
    return manifest


def cleanup_run(run_dir: Path, runs_root: Path) -> None:
    """Delete one prepared run only when it is under the configured runs root."""
    run_dir = run_dir.resolve()
    runs_root = runs_root.resolve()
    if run_dir == runs_root or runs_root not in run_dir.parents:
        raise RunLifecycleError(f"refusing to clean path outside runs root: {run_dir}")
    if run_dir.exists():
        shutil.rmtree(run_dir)


def _write_manifest(run_dir: Path, value: dict[str, Any]) -> None:
    """Atomically write run-manifest.json."""
    target = run_dir / "run-manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
