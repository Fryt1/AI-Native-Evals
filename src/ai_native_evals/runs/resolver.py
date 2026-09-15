"""Load user-facing configuration and resolve one independent Task/Agent run."""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..agents import AgentProfile
from ..plans import TestPlan
from ..providers import (
    ProviderError,
    agent_reasoning_levels,
    agent_wire_apis,
    allowed_reasoning_levels,
    fallback_reasoning_levels,
    load_provider_profiles,
    normalize_level,
    provider_env_file,
    read_provider_env,
    resolve_wire_api,
    validate_model_available,
)
from ..tasks.bundles import TaskBundleError, load_task_bundle
from . import docker_cli
from .spec import RunSpec


class EvalConfigError(ValueError):
    """Raised when eval.yaml or a referenced profile cannot produce a valid run."""


def _local_override_path(config_path: Path) -> Path:
    """The machine-local overlay for a tracked config (``eval.yaml`` -> ``eval.local.yaml``)."""
    return config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")


def _merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base``; mappings merge, everything else replaces."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_config(existing, value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    """Load an evaluation YAML config, then apply its machine-local overlay.

    The tracked ``config/eval.yaml`` describes the framework and holds no
    machine-specific path. Anything that differs per machine -- where the
    evaluated repositories and the runs directory live -- belongs in the
    gitignored ``config/eval.local.yaml``, which overrides the tracked file
    key by key. This keeps the repository shareable while still letting one
    checkout point at whatever local layout it has.
    """
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise EvalConfigError(f"could not read config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvalConfigError("evaluation config must be a YAML object")

    local_path = _local_override_path(path)
    if local_path.is_file():
        try:
            raw_local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise EvalConfigError(f"could not read local config {local_path}: {exc}") from exc
        if not isinstance(raw_local, dict):
            raise EvalConfigError(f"{local_path.name} must be a YAML object")
        value = _merge_config(value, raw_local)
    return value


def resolve_run(
    repo_root: Path,
    task_id: str,
    *,
    config_path: Path | None = None,
    agent: str | None = None,
    agent_version: str | None = None,
    model_profile: str | None = None,
    model_provider: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
    game_engine_ref: str | None = None,
    dsh_ref: str | None = None,
    mcp_profile: str | None = None,
    sandbox_profile: str | None = None,
    preset: str | None = None,
    check_images: bool = False,
) -> RunSpec:
    """Resolve defaults and command-line overrides into one RunSpec.

    ``check_images`` asks Docker whether every image the run will start actually
    exists. It stays off by default so resolution remains usable on a machine
    without Docker (and in tests); callers that are about to launch a run turn
    it on, because a missing image is otherwise discovered only after the run
    has already been recorded as started.
    """
    config_path = config_path or repo_root / "config" / "eval.yaml"
    config = load_config(config_path)
    defaults = _mapping(config, "defaults")
    paths = _mapping(config, "paths")
    agents = _merge_named_profile_source(repo_root, config, "agents")
    model_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "model_profiles",
        # The config key is `models` (see profile_roots in config/eval.yaml) while
        # the inline section this loader was named after is `model_profiles`.
        # Without this the resolver read `profile_roots.model_profiles`, so a
        # machine that pointed `profile_roots.models` elsewhere had its model
        # directory honored by `model list` and ignored by every actual run.
        profile_root_key="models",
        default_root="profiles/models",
    )
    mcp_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "mcp_profiles",
        profile_root_key="mcp",
        default_root="profiles/mcp",
    )
    sandbox_profiles = _merge_named_profile_source(
        repo_root,
        config,
        "sandbox_profiles",
        profile_root_key="sandboxes",
        default_root="profiles/sandboxes",
    )
    presets = _merge_named_profile_source(
        repo_root,
        config,
        "presets",
        profile_root_key="presets",
        default_root="config/presets",
    )

    try:
        provider_profiles = load_provider_profiles(repo_root, config)
    except ProviderError as exc:
        raise EvalConfigError(str(exc)) from exc

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

    # `model_provider` historically named the agent-side route. When it names a
    # configured provider profile, treat it as the upstream selection so both
    # spellings of "pick a provider" reach the same code path.
    if provider is None and model_provider is not None and model_provider in provider_profiles:
        provider = model_provider
        model_provider = None

    agent_name = _select_name(
        agent,
        "agent",
        task_execution,
        defaults,
        preset_config,
        "codex",
        preset_name is not None,
    )
    # A model_profile names a complete model binding, and a `model_provider`
    # that is not a registered provider profile states the route directly. In
    # both cases the whole choice has been made, so the project-level provider
    # default must not be layered on top of it.
    route_is_complete = model_profile is not None or (
        model_provider is not None and model_provider not in provider_profiles
    )
    provider_name = (
        ""
        if provider is None and route_is_complete
        else _select_name(
            provider,
            "provider",
            task_execution,
            defaults,
            preset_config,
            "",
            preset_name is not None,
        )
    )
    provider_config: dict[str, Any] = {}
    if provider_name:
        provider_config = provider_profiles.get(provider_name, {})
        if not provider_config:
            raise EvalConfigError(
                f"unknown provider {provider_name!r}; configured providers: "
                + (", ".join(sorted(provider_profiles)) or "none")
            )
        env_path = provider_env_file(repo_root, provider_config)
        if env_path is None or not env_path.is_file():
            raise EvalConfigError(
                f"provider {provider_name!r} is not configured; create {env_path} "
                "with UPSTREAM_BASE_URL and UPSTREAM_API_KEY"
            )
    selected_model = (
        _select_name(
            model,
            "model",
            task_execution,
            defaults,
            preset_config,
            "",
            preset_name is not None,
        )
        if provider_name
        else ""
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
    if provider_name:
        # A provider selection owns the route. A binding is optional: it only
        # overrides protocol/reasoning defaults when it names the same pair.
        # The model requirement is enforced once the Agent is known to exist,
        # so a bad Agent is reported as such rather than as a missing model.
        profile_name = (
            _matching_binding(model_profiles, provider_name, selected_model)
            or f"{provider_name}:{selected_model}"
        )
    elif model_profile is None and (model_provider is not None or model is not None):
        profile_name = _find_model_profile(
            model_profiles,
            provider=model_provider,
            model=model,
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
    if not model_config and not provider_name:
        raise EvalConfigError(f"unknown model profile: {profile_name}")
    profile_provider = _optional_string(model_config, "provider")
    profile_model = _optional_string(model_config, "model")
    if not provider_name:
        if model_provider is not None and model_provider != profile_provider:
            raise EvalConfigError(
                f"model provider {model_provider!r} does not match profile "
                f"{profile_name!r} provider {profile_provider!r}"
            )
        if model is not None and model != profile_model:
            raise EvalConfigError(
                f"model {model!r} does not match profile "
                f"{profile_name!r} model {profile_model!r}"
            )
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
        if agent_version:
            # One Agent, several versions: the profile says which is the
            # default, an explicit choice re-points the image at another build.
            agent_profile = agent_profile.with_version(agent_version)
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
    resolved_model = model or _string(model_config, "model", "")
    resolved_provider = model_provider or profile_provider
    resolved_env_file: str | None = None
    resolved_reasoning = _optional_string(
        model_config,
        "reasoning_effort",
        _optional_string(agent_config, "reasoning_effort"),
    )
    if provider_name:
        if not selected_model:
            raise EvalConfigError(
                f"provider {provider_name!r} needs a model; "
                "list them with the provider's /v1/models endpoint"
            )
        env_path = provider_env_file(repo_root, provider_config)
        if env_path is not None:
            missing = validate_model_available(
                provider_config, read_provider_env(env_path), selected_model
            )
            if missing:
                raise EvalConfigError(missing)
        negotiated = resolve_wire_api(provider_config, agent_name, agent_profile.adapter)
        if negotiated is None:
            raise EvalConfigError(
                f"provider {provider_name!r} speaks "
                + ", ".join(_as_list(provider_config.get("wire_api")))
                + f" but agent {agent_name!r} speaks "
                + ", ".join(_as_list(agent_wire_apis(agent_name, agent_profile.adapter)))
                + "; no protocol is shared"
            )
        protocol = _string(model_config, "protocol", negotiated)
        resolved_model = selected_model
        resolved_provider = None
        env_path = provider_env_file(repo_root, provider_config)
        resolved_env_file = str(env_path) if env_path else None
        agent_levels = agent_reasoning_levels(agent_name, agent_profile.adapter)
        allowed = allowed_reasoning_levels(
            fallback_reasoning_levels(provider_config), agent_levels
        )
        requested = (
            _select_name(
                reasoning_effort,
                "reasoning_effort",
                task_execution,
                defaults,
                preset_config,
                "",
                preset_name is not None,
            )
            or _optional_string(model_config, "reasoning_effort")
            or _optional_string(provider_config, "default_reasoning")
            or ""
        )
        # Accept a level the other side spells differently (`none` vs `off`)
        # but store it the way this Agent names it, since the value is written
        # into that Agent's own configuration.
        matched = None
        if requested:
            wanted = normalize_level(requested)
            matched = next(
                (level for level in allowed if normalize_level(level) == wanted), None
            )
        if requested and allowed and matched is None:
            raise EvalConfigError(
                f"reasoning effort {requested!r} is not available for provider "
                f"{provider_name!r} with agent {agent_name!r}; choose one of: "
                + ", ".join(allowed)
            )
        resolved_reasoning = matched or requested or None
    spec = RunSpec(
        run_id=run_id,
        task_id=task_id,
        task_prompt=task_bundle.prompt,
        agent=agent_name,
        agent_image=agent_profile.image,
        model_profile=profile_name,
        model=resolved_model,
        # Only what the model binding or the operator actually selected. This
        # used to fall back to `"eval"` for the DSH adapter, because the Codex
        # config renderer needs a route name -- but that renderer already
        # defaults to `eval` itself, and no DSH entry point reads the value at
        # all. The fallback therefore changed nothing about how either Agent ran
        # while making `model_provider` an Agent-specific value, and since this
        # field is part of the Console's fairness fingerprint, every Codex-vs-DSH
        # comparison was declared "inputs differ, compare with caution" even when
        # every real input was identical. Agent identity must not leak into a
        # field whose whole job is to describe the *model* side of the run.
        model_provider=resolved_provider,
        protocol=protocol,
        reasoning_effort=resolved_reasoning,
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
        source_roots=source_roots,
        task_bundle=task_bundle.to_dict(),
        preset=preset_name,
        provider=provider_name or None,
        provider_env_file=resolved_env_file,
    )
    if check_images:
        _require_run_images(spec)
    return spec


def _require_run_images(spec: RunSpec) -> None:
    """Fail resolution when an image this run needs was never built.

    Only a definite absence fails. A machine where Docker cannot be asked
    resolves normally, because "I could not check" is not evidence that
    something is missing.
    """
    from .images import image_paths, missing_images

    absent = missing_images(image_paths(spec))
    if not absent:
        return
    detail = "; ".join(
        f"{status.reference} ({status.error})" if status.error else status.reference
        for status in absent
    )
    raise EvalConfigError(
        "the following Docker images do not exist on this machine: "
        + detail
        + f". Build them with: {docker_cli.build_command()}"
    )


def _as_list(value: Any) -> list[str]:
    """Render a scalar-or-list profile field as a list of strings."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _matching_binding(
    profiles: Mapping[str, Any],
    provider: str,
    model: str,
) -> str | None:
    """Return the binding naming this exact provider/model pair, if one exists."""
    for profile_id, value in profiles.items():
        if not isinstance(value, Mapping):
            continue
        if str(value.get("provider")) != provider or str(value.get("model")) != model:
            continue
        return str(profile_id)
    return None


def _find_model_profile(
    profiles: Mapping[str, Any],
    *,
    provider: str | None,
    model: str | None,
) -> str:
    """Find the configured binding for an independent Provider/Model selection."""
    matches = []
    for profile_id, value in profiles.items():
        if not isinstance(value, Mapping):
            continue
        if provider is not None and str(value.get("provider")) != provider:
            continue
        if model is not None and str(value.get("model")) != model:
            continue
        matches.append(str(profile_id))
    if not matches:
        raise EvalConfigError(f"no model profile matches provider={provider!r}, model={model!r}")
    if len(matches) > 1:
        raise EvalConfigError(
            "model selection is ambiguous; choose a model profile binding from: "
            + ", ".join(sorted(matches))
        )
    return matches[0]


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
    *,
    profile_root_key: str | None = None,
    default_root: str | None = None,
) -> dict[str, Any]:
    """Load profile files from the configured root directory.

    The single implementation of "where do profiles of this kind live": every
    caller passes the root key its own configuration section uses, rather than
    pre-merging an overlay it read for itself.
    """
    merged: dict[str, Any] = {}
    root = resolve_profile_root(
        repo_root,
        config,
        profile_root_key or inline_key,
        default_root=default_root
        or ("profiles/agents" if inline_key == "agents" else "profiles/models"),
    )
    if root.is_dir():
        for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, Mapping):
                raise EvalConfigError(f"profile file must contain a mapping: {path}")
            profile_id = payload.get("id", path.stem)
            merged[str(profile_id)] = dict(payload)
    return merged


def _source_roots(paths: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve resource id -> location, letting the environment win.

    Precedence is environment over config, so a CI job can point ids at a Git
    URL without editing any file:

        AI_NATIVE_EVALS_SOURCE_<ID>       -> the location
        AI_NATIVE_EVALS_SOURCE_REF_<ID>   -> an optional default ref

    The tracked config ships with no ids at all; a machine supplies them in
    ``config/eval.local.yaml``.
    """
    raw = paths.get("source_roots")
    if isinstance(raw, Mapping):
        roots: dict[str, Any] = {k: v for k, v in raw.items() if v is not None}
    else:
        roots = {
            key: value
            for key, value in paths.items()
            if key not in {"runs_root", "source_roots"} and isinstance(value, str)
        }
    return _apply_source_env(roots)


def _apply_source_env(roots: dict[str, Any]) -> dict[str, Any]:
    """Overlay ``AI_NATIVE_EVALS_SOURCE_*`` variables onto configured roots."""
    prefix = "AI_NATIVE_EVALS_SOURCE_"
    ref_prefix = f"{prefix}REF_"
    resolved = dict(roots)
    for name, value in os.environ.items():
        if not name.startswith(prefix) or name.startswith(ref_prefix):
            continue
        if not value.strip():
            continue
        source_id = name[len(prefix) :].lower()
        existing = resolved.get(source_id)
        ref = os.environ.get(f"{ref_prefix}{name[len(prefix):]}")
        if isinstance(existing, Mapping):
            merged = dict(existing)
            merged["url"] = value.strip()
            if ref and ref.strip():
                merged["ref"] = ref.strip()
            resolved[source_id] = merged
        else:
            resolved[source_id] = (
                {"url": value.strip(), "ref": ref.strip()}
                if ref and ref.strip()
                else value.strip()
            )
    return resolved


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


def resolve_runs_root(
    repo_root: Path,
    config: Mapping[str, Any],
    *,
    override: Path | None = None,
) -> Path:
    """Where runs are written: the override, ``paths.runs_root``, or the default.

    Lives here, beside :func:`resolve_run`, because the evaluator must not reach
    into the Console package to learn where its own runs go. ``preflight`` used to
    do exactly that, which put a dependency on the projection layer inside the
    evaluation layer.
    """
    if override is not None:
        return override.expanduser().resolve()
    paths = _mapping(config, "paths")
    raw = paths.get("runs_root")
    if isinstance(raw, str) and raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate.resolve()
    return (repo_root / ".." / "EvalRuns").resolve()


def resolve_profile_root(
    repo_root: Path,
    config: Mapping[str, Any],
    kind: str,
    *,
    default_root: str | None = None,
) -> Path:
    """Resolve ``profile_roots.<kind>`` to a directory.

    One resolver for every consumer. The CLI, the Console registry and the Agent
    profile loader each grew their own copy of these four lines, and a change to
    how a root is spelled reached whichever copy the author happened to be
    looking at.
    """
    roots = _mapping(config, "profile_roots")
    raw = roots.get(kind)
    fallback = default_root or f"profiles/{kind}"
    candidate = Path(str(raw)) if isinstance(raw, str) and raw else Path(fallback)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


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
