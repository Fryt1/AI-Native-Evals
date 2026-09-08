"""Run preparation, manifest persistence, and cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..mcp import project_dsh_mcp_servers
from ..resources import prepare_resources
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
    """Create a run workspace and write its resolved manifest.

    Only resources declared by the Task bundle are copied. ``game-engine`` and
    ``dsh`` aliases are retained in the manifest solely for compatibility with
    older evaluators; they are not implicit dependencies anymore.
    """
    repo_root = repo_root.resolve()
    run_dir = spec.run_dir.resolve()
    if run_dir.exists():
        raise RunLifecycleError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    workspace_dir = run_dir / "workspace"
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

    resource_specs = spec.resource_specs
    if game_engine_ref is not None:
        resource_specs = _with_ref(resource_specs, "game-engine", game_engine_ref)
    if dsh_ref is not None:
        resource_specs = _with_ref(resource_specs, "dsh", dsh_ref)
    resource_snapshots, resource_paths = prepare_resources(resource_specs, workspace_dir)

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
    agent_profile_path = agent_config_dir / "agent-profile.json"
    agent_profile_path.write_text(
        json.dumps(spec.agent_profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evaluator_profile_path = agent_config_dir / "evaluator-agent-profile.json"
    evaluator_profile_path.write_text(
        json.dumps(spec.evaluator_agent_profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    system_prompt_path = agent_config_dir / "agent-system-prompt.txt"
    if spec.agent_profile.system_prompt:
        system_prompt_path.write_text(spec.agent_profile.system_prompt, encoding="utf-8")
    dsh_runner_source = repo_root / "docker" / "dsh-agent" / "acp-runner.mjs"
    dsh_runner_path = agent_config_dir / "dsh-acp-runner.mjs"
    if dsh_runner_source.is_file():
        shutil.copy2(dsh_runner_source, dsh_runner_path)

    project_dir = resource_paths.get("game-engine")
    dsh_dir = resource_paths.get("dsh")
    paths: dict[str, Any] = {
        "project": str(project_dir) if project_dir else None,
        "dsh": str(dsh_dir) if dsh_dir else None,
        "workspace": str(workspace_dir),
        "output": str(workspace_dir / "output"),
        "scratch": str(workspace_dir / "scratch"),
        "artifacts": str(workspace_dir / "artifacts"),
        "evidence": str(workspace_dir / "evidence"),
        "trace": str(workspace_dir / "trace"),
        "agent_config": str(agent_config_dir),
        "mcp_servers": str(mcp_config_path),
        "dsh_mcp_servers": str(dsh_mcp_config_path),
        "agent_profile": str(agent_profile_path),
        "evaluator_agent_profile": str(evaluator_profile_path),
        "system_prompt": str(system_prompt_path) if system_prompt_path.is_file() else None,
        "dsh_runner": str(dsh_runner_path) if dsh_runner_path.is_file() else None,
        "resources": {key: str(value) for key, value in resource_paths.items()},
    }

    run_metadata = spec.to_dict()
    run_metadata["task_prompt"] = _render_task_prompt(
        str(run_metadata.get("task_prompt", "")),
        run_id=spec.run_id,
        workspace_dir=workspace_dir,
    )
    # Persist the effective refs used for this run, not only the resolved spec
    # that existed before CLI ref overrides were applied.
    run_metadata["resource_specs"] = [spec.to_dict() for spec in resource_specs]
    manifest: dict[str, Any] = {
        "status": "prepared",
        "run": run_metadata,
        "snapshots": {
            "resources": {key: value.to_dict() for key, value in resource_snapshots.items()},
            # Compatibility projections for the old verifier/report vocabulary.
            "game_engine": _legacy_snapshot(resource_snapshots.get("game-engine")),
            "ai_native_dsh": _legacy_snapshot(resource_snapshots.get("dsh")),
        },
        "paths": paths,
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
        .replace("${WORKSPACE}", "/workspace")
        .replace("${HOST_WORKSPACE}", str(workspace_dir))
        .replace("${HOST_WORKSPACE_POSIX}", workspace_dir.as_posix())
    )


def _legacy_snapshot(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    value = snapshot.to_dict()
    value.update(snapshot.snapshot)
    return value


def _with_ref(specs: tuple[Any, ...], resource_id: str, ref: str) -> tuple[Any, ...]:
    from dataclasses import replace

    return tuple(
        replace(spec, ref=ref) if spec.resource_id == resource_id else spec
        for spec in specs
    )


def _write_manifest(run_dir: Path, value: dict[str, Any]) -> None:
    """Atomically write run-manifest.json."""
    target = run_dir / "run-manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
