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
    # The task prompt is written to a file and read from there, not passed as a
    # command-line argument. Prompts are Markdown: they contain newlines, pipes,
    # backticks and lists. Handing that to `docker run` as one argument loses
    # structure -- a table's rows vanished and the Agent reported the missing
    # names as an ambiguity in the request rather than a delivery fault.
    task_prompt_path = agent_config_dir / "task-prompt.md"
    task_prompt_path.write_text(spec.task_prompt, encoding="utf-8")
    dsh_runner_source = repo_root / "docker" / "dsh-agent" / "acp-runner.mjs"
    dsh_runner_path = agent_config_dir / "dsh-acp-runner.mjs"
    if dsh_runner_source.is_file():
        shutil.copy2(dsh_runner_source, dsh_runner_path)
    staged_attachments = _stage_attachments(
        repo_root, agent_config_dir, spec.agent_profile.attach, spec.source_roots
    )

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
        # Where attachments were staged, inside the mounted config directory.
        "attach": [str(path) for path in staged_attachments],
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
        # A tag is not an identity: rebuilding keeps the tag and changes the
        # bytes. Recording the resolved image IDs is what lets a later reader
        # say which build this run actually used.
        "images": _image_identities(spec),
        "snapshots": {
            "resources": {key: value.to_dict() for key, value in resource_snapshots.items()},
        },
        "paths": paths,
    }
    _write_manifest(run_dir, manifest)
    return run_dir


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a run manifest from a directory or manifest path."""
    manifest_path = path / "run-manifest.json" if path.is_dir() else path
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _image_identities(spec: RunSpec) -> dict[str, Any]:
    """Resolve the images this run starts to their immutable IDs.

    Best effort by design: preparing a run must not fail because Docker is
    absent, and an unknown ID is recorded as such rather than as ``None`` so a
    reader can tell "not checked" from "no image".
    """
    from .images import image_paths, inspect_image

    entries: dict[str, Any] = {}
    for role, reference in image_paths(spec):
        status = inspect_image(reference)
        entry: dict[str, Any] = {"reference": reference}
        if status.present is True:
            entry["id"] = status.image_id
            entry["present"] = True
        elif status.present is False:
            entry["present"] = False
            entry["error"] = status.error
        else:
            entry["present"] = None
            entry["error"] = status.error
        entries[role] = entry
    return entries


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


def _stage_attachments(
    repo_root: Path,
    agent_config_dir: Path,
    attach: tuple[str, ...],
    source_roots: dict[str, Any] | None = None,
) -> list[Path]:
    """Copy declared attachments where the Agent's own startup can find them.

    The framework delivers files and stops there. What an attachment *is* --
    a plugin, a rule file, a dataset -- is the Agent's business, because only
    the Agent knows how its own composition works. Naming this "plugins" put one
    Agent's mechanism into the framework's vocabulary, which is exactly the
    coupling this repository exists to avoid.

    A spec is `source_id` or `source_id@subdirectory`. The id resolves through
    `paths.source_roots`, the same place a Task's repositories resolve, so an
    attachment keeps its own repository and this one only records where it lives.

    An id that is not configured, or whose directory does not exist, is skipped
    rather than guessed at: staging the wrong tree would surface later as the
    Agent failing to start, which is much harder to read than a missing file.
    """
    if not attach:
        return []
    roots = source_roots or {}
    staged: list[Path] = []
    target_root = agent_config_dir / "attach"
    for spec in attach:
        source_id, _, name = spec.partition("@")
        source_id = source_id.strip()
        location = roots.get(source_id)
        if not isinstance(location, str) or not location.strip():
            continue
        source = Path(location)
        if not source.is_absolute():
            source = (repo_root / source).resolve()
        if not source.is_dir():
            continue
        destination = target_root / (name.strip() or source_id)
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                "node_modules", ".git", "*.tsbuildinfo", "*.map", "src", "*.ts"
            ),
        )
        staged.append(destination)
    return staged


def _render_task_prompt(prompt: str, *, run_id: str, workspace_dir: Path) -> str:
    """Resolve run-local placeholders used by host-aware task prompts."""
    return (
        prompt.replace("${RUN_ID}", run_id)
        .replace("${WORKSPACE}", "/workspace")
        .replace("${HOST_WORKSPACE}", str(workspace_dir))
        .replace("${HOST_WORKSPACE_POSIX}", workspace_dir.as_posix())
    )


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
