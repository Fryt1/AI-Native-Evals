"""Load user-facing configuration and resolve one independent Task/Agent run."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..agents import AgentProfile
from ..plans import TestPlan
from ..tasks.bundles import TaskBundleError, load_task_bundle
from .spec import RunSpec


class EvalConfigError(ValueError):
    """Raised when eval.yaml or a referenced profile cannot produce a valid run."""


def load_config(path: Path) -> dict[str, Any]:
    """Load an evaluation YAML config."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
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
    sandbox_profile: str | None = None,
    preset: str | None = None,
) -> RunSpec:
    """Resolve defaults and command-line overrides into one RunSpec."""
    config_path = config_path or repo_root / "config" / "eval.yaml"
    config = load_config(config_path)
    defaults = _mapping(config, "defaults")
    paths = _mapping(config, "paths")
    profiles_config = _mapping(config, "profiles")
    agents = _merge_named_profile_source(
        repo_root,
        config,
        "agents",
        {**_mapping(profiles_config, "agents"), **_mapping(config, "agents")},
    )
    model_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "model_profiles",
        {**_mapping(profiles_config, "models"), **_mapping(config, "model_profiles")},
    )
    mcp_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "mcp_profiles",
        {**_mapping(profiles_config, "mcp"), **_mapping(config, "mcp_profiles")},
        profile_root_key="mcp",
        default_root="profiles/mcp",
    )
    sandbox_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "sandbox_profiles",
        {**_mapping(profiles_config, "sandboxes"), **_mapping(config, "sandbox_profiles")},
        profile_root_key="sandboxes",
        default_root="profiles/sandboxes",
    )
    presets = _merge_named_profile_source(
        repo_root,
        config,
        "presets",
        {**_mapping(profiles_config, "presets"), **_mapping(config, "presets")},
        profile_root_key="presets",
        default_root="config/presets",
    )

    try:
        task_bundle = load_task_bundle(repo_root, task_id, config=config)
    except TaskBundleError as exc:
        raise EvalConfigError(str(exc)) from exc
    task_execution = task_bundle.execution
    preset_name = preset or _optional_string(task_execution, "preset")
    preset_config: dict[str, Any] = {}
    if preset_name:
        preset_config = _mapping(presets, preset_name)
        if not preset_config:
            raise EvalConfigError(f"unknown run preset: {preset_name}")

    agent_name = _select_name(
        agent,
        "agent",
        task_execution,
        defaults,
        preset_config,
        "codex",
        preset_name is not None,
    )
    profile_name = _select_name(
        model_profile,
        "model_profile",
        task_execution,
        defaults,
        preset_config,
        "default",
        preset_name is not None,
    )
    mcp_name = _select_name(
        mcp_profile,
        "mcp_profile",
        task_execution,
        defaults,
        preset_config,
        "none",
        preset_name is not None,
    )
    sandbox_name = _select_name(
        sandbox_profile,
        "sandbox_profile",
        task_execution,
        defaults,
        preset_config,
        "",
        preset_name is not None,
    )
    snapshot_mode = _string(
        task_execution,
        "snapshot_mode",
        _string(defaults, "snapshot_mode", "working_tree"),
    )
    try:
        test_plan = TestPlan.from_mapping(task_bundle.test_plan)
    except ValueError as exc:
        raise EvalConfigError(str(exc)) from exc

    evaluator_agent_name = _string(defaults, "evaluator_agent", "codex")
    agent_config = _mapping(agents, agent_name)
    evaluator_agent_config = _mapping(agents, evaluator_agent_name)
    model_config = _mapping(model_profiles, profile_name)
    mcp_config = _mapping(mcp_profiles, mcp_name)
    if not agent_config:
        raise EvalConfigError(f"unknown agent profile: {agent_name}")
    if sandbox_name and sandbox_name != "inline":
        sandbox_config = _mapping(sandbox_profiles, sandbox_name)
        if not sandbox_config:
            raise EvalConfigError(f"unknown sandbox profile: {sandbox_name}")
    else:
        sandbox_config = _mapping(config, "sandbox")
    if not evaluator_agent_config:
        raise EvalConfigError(f"unknown evaluator agent profile: {evaluator_agent_name}")
    if not model_config:
        raise EvalConfigError(f"unknown model profile: {profile_name}")
    if not mcp_config:
        raise EvalConfigError(f"unknown MCP profile: {mcp_name}")
    if "adapter" not in agent_config:
        agent_config = {**agent_config, "adapter": agent_name}
    if "adapter" not in evaluator_agent_config:
        evaluator_agent_config = {
            **evaluator_agent_config,
            "adapter": evaluator_agent_name,
        }
    try:
        agent_profile = AgentProfile.from_mapping(agent_name, agent_config)
    except ValueError as exc:
        raise EvalConfigError(str(exc)) from exc
    try:
        evaluator_agent_profile = AgentProfile.from_mapping(
            evaluator_agent_name, evaluator_agent_config
        )
    except ValueError as exc:
        raise EvalConfigError(str(exc)) from exc
    mcp_servers = _resolve_mcp_servers(mcp_config)

    source_roots = _source_roots(paths)
    try:
        resource_specs = task_bundle.resource_specs(
            repo_root=repo_root,
            source_roots=source_roots,
        )
    except ValueError as exc:
        raise EvalConfigError(str(exc)) from exc
    game_engine_root = _resource_source(resource_specs, "game-engine")
    dsh_root = _resource_source(resource_specs, "dsh")
    if game_engine_ref is not None:
        resource_specs = _override_resource_ref(resource_specs, "game-engine", game_engine_ref)
    if dsh_ref is not None:
        resource_specs = _override_resource_ref(resource_specs, "dsh", dsh_ref)

    runs_root = _resolve_path(repo_root, paths, "runs_root")
    run_id = f"{task_id}-{uuid4().hex[:10]}"
    protocol = _string(model_config, "protocol", agent_profile.protocol)
    model = _string(model_config, "model", "")
    return RunSpec(
        run_id=run_id,
        task_id=task_id,
        task_prompt=task_bundle.prompt,
        agent=agent_name,
        agent_image=agent_profile.image,
        model_profile=profile_name,
        model=model,
        model_provider=_optional_string(
            model_config,
            "provider",
            "eval" if agent_profile.adapter == "dsh-acp" else None,
        ),
        protocol=protocol,
        reasoning_effort=_optional_string(
            model_config,
            "reasoning_effort",
            _optional_string(agent_config, "reasoning_effort"),
        ),
        mcp_profile=mcp_name,
        mcp_host=_string(mcp_config, "host", "host.docker.internal"),
        mcp_port=_integer(mcp_config, "port", 9876),
        mcp_blender="blender" in mcp_servers,
        mcp_ue5="unreal-mcp" in mcp_servers,
        mcp_servers=mcp_servers,
        verify=task_bundle.verify,
        test_plan=test_plan,
        sandbox=sandbox_config,
        sandbox_profile=sandbox_name or "inline",
        snapshot_mode=snapshot_mode,
        game_engine_root=game_engine_root,
        dsh_root=dsh_root,
        runs_root=runs_root,
        run_dir=runs_root / run_id,
        agent_profile=agent_profile,
        evaluator_agent=evaluator_agent_name,
        evaluator_agent_profile=evaluator_agent_profile,
        resource_specs=resource_specs,
        task_bundle=task_bundle.to_dict(),
        preset=preset_name,
    )


def _select_name(
    explicit: str | None,
    key: str,
    task_execution: Mapping[str, Any],
    defaults: Mapping[str, Any],
    preset: Mapping[str, Any],
    fallback: str,
    has_explicit_preset: bool,
) -> str:
    """Resolve one selector without duplicating low-level profile fields."""
    if explicit is not None:
        return explicit
    if has_explicit_preset and key in preset:
        return _string(preset, key, fallback)
    if key in task_execution:
        return _string(task_execution, key, fallback)
    if key in preset:
        return _string(preset, key, fallback)
    return _string(defaults, key, fallback)


def _merge_named_profile_source(
    repo_root: Path,
    config: Mapping[str, Any],
    inline_key: str,
    root_value: Mapping[str, Any],
    *,
    profile_root_key: str | None = None,
    default_root: str | None = None,
) -> dict[str, Any]:
    """Load external profile files and overlay inline legacy definitions."""
    merged: dict[str, Any] = {}
    profile_roots = _mapping(config, "profile_roots")
    root_key = profile_root_key or inline_key
    fallback_root = default_root or (
        "profiles/agents" if inline_key == "agents" else "profiles/models"
    )
    raw_root = profile_roots.get(root_key, fallback_root)
    root = Path(str(raw_root))
    if not root.is_absolute():
        root = repo_root / root
    if root.is_dir():
        for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, Mapping):
                raise EvalConfigError(f"profile file must contain a mapping: {path}")
            profile_id = payload.get("id", path.stem)
            merged[str(profile_id)] = dict(payload)
    for key, value in root_value.items():
        if isinstance(value, Mapping):
            current = merged.get(str(key), {})
            merged[str(key)] = {**current, **dict(value)}
    return merged


def _source_roots(paths: Mapping[str, Any]) -> dict[str, Any]:
    raw = paths.get("source_roots")
    if isinstance(raw, Mapping):
        return dict(raw)
    return {
        key: value
        for key, value in paths.items()
        if key not in {"runs_root", "source_roots"} and isinstance(value, str)
    }


def _resource_source(specs: tuple[Any, ...], resource_id: str) -> Path | None:
    for spec in specs:
        if spec.resource_id == resource_id:
            return spec.source
    return None


def _override_resource_ref(
    specs: tuple[Any, ...], resource_id: str, ref: str
) -> tuple[Any, ...]:
    return tuple(
        replace(spec, ref=ref) if spec.resource_id == resource_id else spec
        for spec in specs
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
    """Substitute ${NAME} placeholders in MCP profile strings."""
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace(f"${{{name}}}", replacement)
        return value
    if isinstance(value, list):
        return [_substitute_mcp_values(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_mcp_values(item, variables) for key, item in value.items()}
    return value


def _mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key, {})
    return dict(child) if isinstance(child, Mapping) else {}


def _string(value: Mapping[str, Any], key: str, default: str) -> str:
    child = value.get(key, default)
    return str(child) if child is not None else default


def _optional_string(
    value: Mapping[str, Any], key: str, default: str | None = None
) -> str | None:
    child = value.get(key, default)
    return None if child is None else str(child)


def _integer(value: Mapping[str, Any], key: str, default: int) -> int:
    child = value.get(key, default)
    try:
        return int(child)
    except (TypeError, ValueError) as exc:
        raise EvalConfigError(f"{key} must be an integer") from exc


def _resolve_path(repo_root: Path, paths: Mapping[str, Any], key: str) -> Path:
    raw = paths.get(key)
    if raw is None:
        raw = _mapping(paths, "source_roots").get(key)
    if not isinstance(raw, str) or not raw:
        raise EvalConfigError(f"paths.{key} must be a non-empty string")
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()
