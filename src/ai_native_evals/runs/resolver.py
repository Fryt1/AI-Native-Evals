"""Load the small user-facing eval.yaml and resolve a RunSpec."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .spec import RunSpec


class EvalConfigError(ValueError):
    """Raised when eval.yaml cannot produce a valid run."""


def load_config(path: Path) -> dict[str, Any]:
    """Load an evaluation YAML config."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise EvalConfigError(f"could not read config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvalConfigError("evaluation config must be a YAML object")
    return value


def resolve_run(
    repo_root: Path,
    task_id: str,
    *,
    config_path: Path | None = None,
    agent: str | None = None,
    model_profile: str | None = None,
    game_engine_ref: str | None = None,
    dsh_ref: str | None = None,
    mcp_profile: str | None = None,
) -> RunSpec:
    """Resolve defaults and command-line overrides into one RunSpec."""
    del game_engine_ref, dsh_ref  # Refs are consumed by snapshot creation.
    config_path = config_path or repo_root / "config" / "eval.yaml"
    config = load_config(config_path)
    defaults = _mapping(config, "defaults")
    paths = _mapping(config, "paths")
    agents = _mapping(config, "agents")
    profiles = _mapping(config, "model_profiles")
    mcp_profiles = _mapping(config, "mcp_profiles")
    tasks = _mapping(config, "tasks")
    sandbox_config = _mapping(config, "sandbox")

    task_config = _mapping(tasks, task_id)
    agent_name = agent or _string(
        task_config,
        "agent",
        _string(defaults, "agent", "codex"),
    )
    profile_name = model_profile or _string(defaults, "model_profile", "default")
    mcp_name = mcp_profile or _string(
        task_config,
        "mcp_profile",
        _string(defaults, "mcp_profile", "default"),
    )
    snapshot_mode = _string(defaults, "snapshot_mode", "working_tree")

    agent_config = _mapping(agents, agent_name)
    profile = _mapping(profiles, profile_name)
    mcp_config = _mapping(mcp_profiles, mcp_name)
    mcp_servers = _resolve_mcp_servers(mcp_config)
    if not profile:
        raise EvalConfigError(f"unknown model profile: {profile_name}")
    if not agent_config:
        raise EvalConfigError(f"unknown agent: {agent_name}")
    if not mcp_config:
        raise EvalConfigError(f"unknown MCP profile: {mcp_name}")

    game_engine_root = _resolve_path(repo_root, paths, "game_engine")
    dsh_root = _resolve_path(repo_root, paths, "dsh")
    runs_root = _resolve_path(repo_root, paths, "runs_root")
    run_id = f"{task_id}-{uuid4().hex[:10]}"

    return RunSpec(
        run_id=run_id,
        task_id=task_id,
        task_prompt=_string(
            task_config,
            "prompt",
            f"Execute evaluation task '{task_id}' in /workspace/game-engine. "
            "Read the project instructions before making changes and report only "
            "after verification.",
        ),
        agent=agent_name,
        agent_image=_string(agent_config, "image", f"ai-native-{agent_name}-agent:local"),
        model_profile=profile_name,
        model=_string(profile, "model", ""),
        protocol=_string(profile, "protocol", "responses"),
        reasoning_effort=_optional_string(profile, "reasoning_effort"),
        mcp_profile=mcp_name,
        mcp_host=_string(mcp_config, "host", "host.docker.internal"),
        mcp_port=_integer(mcp_config, "port", 9876),
        mcp_blender="blender" in mcp_servers,
        mcp_ue5="unreal-mcp" in mcp_servers,
        mcp_servers=mcp_servers,
        sandbox=sandbox_config,
        snapshot_mode=snapshot_mode,
        game_engine_root=game_engine_root,
        dsh_root=dsh_root,
        runs_root=runs_root,
        run_dir=runs_root / run_id,
    )


def _resolve_mcp_servers(mcp_config: dict[str, Any]) -> dict[str, Any]:
    """Resolve one MCP profile into JSON-safe server descriptors."""
    raw_servers = mcp_config.get("servers", {})
    if not isinstance(raw_servers, dict):
        raise EvalConfigError("MCP profile servers must be a mapping")

    variables: dict[str, str] = {
        "MCP_HOST": _string(mcp_config, "host", "host.docker.internal"),
        "MCP_PORT": str(_integer(mcp_config, "port", 9876)),
    }
    raw_variables = mcp_config.get("variables", {})
    if isinstance(raw_variables, dict):
        variables.update({str(key): str(value) for key, value in raw_variables.items()})

    resolved: dict[str, Any] = {}
    for name, descriptor in raw_servers.items():
        if not isinstance(name, str) or not name:
            raise EvalConfigError("MCP server names must be non-empty strings")
        if not isinstance(descriptor, dict):
            raise EvalConfigError(f"MCP server {name!r} must be a mapping")
        resolved[name] = _substitute_mcp_values(copy.deepcopy(descriptor), variables)
    return resolved


def _substitute_mcp_values(value: Any, variables: dict[str, str]) -> Any:
    """Substitute `${NAME}` placeholders in MCP profile strings."""
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace(f"${{{name}}}", replacement)
        return value
    if isinstance(value, list):
        return [_substitute_mcp_values(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            key: _substitute_mcp_values(item, variables) for key, item in value.items()
        }
    return value


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key, {})
    return child if isinstance(child, dict) else {}


def _string(value: dict[str, Any], key: str, default: str) -> str:
    child = value.get(key, default)
    return str(child) if child is not None else default


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    child = value.get(key)
    return None if child is None else str(child)


def _integer(value: dict[str, Any], key: str, default: int) -> int:
    child = value.get(key, default)
    try:
        return int(child)
    except (TypeError, ValueError) as exc:
        raise EvalConfigError(f"{key} must be an integer") from exc


def _resolve_path(repo_root: Path, paths: dict[str, Any], key: str) -> Path:
    raw = paths.get(key)
    if not isinstance(raw, str) or not raw:
        raise EvalConfigError(f"paths.{key} must be a non-empty string")
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()
