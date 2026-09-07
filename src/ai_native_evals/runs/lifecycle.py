"""Manual run lifecycle: prepare, status, cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..mcp import project_dsh_mcp_servers
from .snapshots import snapshot_repository
from .spec import RunSpec


class RunLifecycleError(RuntimeError):
    """Raised for invalid run lifecycle operations."""


def prepare_run(
    repo_root: Path,
    spec: RunSpec,
    *,
    game_engine_ref: str | None = None,
    dsh_ref: str | None = None,
) -> Path:
    """Create a run workspace and write its resolved manifest."""
    run_dir = spec.run_dir.resolve()
    if run_dir.exists():
        raise RunLifecycleError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    workspace_dir = run_dir / "workspace"
    project_dir = workspace_dir / "game-engine"
    dsh_dir = workspace_dir / "ai-native-dsh"
    agent_config_dir = workspace_dir / "agent-config"
    for child in (
        workspace_dir,
        workspace_dir / "output",
        workspace_dir / "scratch",
        workspace_dir / "artifacts",
        workspace_dir / "evidence",
        workspace_dir / "trace",
        agent_config_dir,
    ):
        child.mkdir(parents=True)

    game_snapshot = snapshot_repository(spec.game_engine_root, project_dir, game_engine_ref)
    dsh_snapshot = None
    if spec.dsh_root.is_dir():
        dsh_snapshot = snapshot_repository(spec.dsh_root, dsh_dir, dsh_ref)

    mcp_config_path = agent_config_dir / "mcp-servers.json"
    mcp_config_path.write_text(
        json.dumps(spec.mcp_servers, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    dsh_mcp_config_path = agent_config_dir / "dsh-mcp-servers.json"
    dsh_mcp_config_path.write_text(
        json.dumps(project_dsh_mcp_servers(spec.mcp_servers), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    run_metadata = spec.to_dict()
    # Task prompts may refer to the real Windows path used by host DCCs. Keep
    # the resolved value in the manifest so the exact prompt is reproducible.
    run_metadata["task_prompt"] = _render_task_prompt(
        str(run_metadata.get("task_prompt", "")),
        run_id=spec.run_id,
        workspace_dir=workspace_dir,
    )
    manifest: dict[str, Any] = {
        "status": "prepared",
        "run": run_metadata,
        "snapshots": {
            "game_engine": game_snapshot,
            "ai_native_dsh": dsh_snapshot,
        },
        "paths": {
            "project": str(project_dir),
            "dsh": str(dsh_dir) if dsh_snapshot else None,
            "workspace": str(workspace_dir),
            "output": str(workspace_dir / "output"),
            "scratch": str(workspace_dir / "scratch"),
            "artifacts": str(workspace_dir / "artifacts"),
            "evidence": str(workspace_dir / "evidence"),
            "trace": str(workspace_dir / "trace"),
            "agent_config": str(agent_config_dir),
            "mcp_servers": str(mcp_config_path),
            "dsh_mcp_servers": str(dsh_mcp_config_path),
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
    return update_manifest(run_dir, status=status)


def update_manifest(
    run_dir: Path,
    *,
    status: str | None = None,
    runtime: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update lifecycle fields while preserving the immutable run metadata."""
    manifest = load_manifest(run_dir)
    if status is not None:
        manifest["status"] = status
    if runtime is not None:
        manifest["runtime"] = runtime
    if evaluation is not None:
        manifest["evaluation"] = evaluation
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


def _render_task_prompt(prompt: str, *, run_id: str, workspace_dir: Path) -> str:
    """Resolve run-local placeholders used by host-aware task prompts."""
    return (
        prompt.replace("${RUN_ID}", run_id)
        .replace("${HOST_WORKSPACE}", str(workspace_dir))
        .replace("${HOST_WORKSPACE_POSIX}", workspace_dir.as_posix())
    )


def _write_manifest(run_dir: Path, value: dict[str, Any]) -> None:
    """Atomically write run-manifest.json."""
    target = run_dir / "run-manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
