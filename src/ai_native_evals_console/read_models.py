"""Frontend-facing projections assembled from EvalRuns files."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import yaml

from ai_native_evals.providers import (
    ProviderError,
    load_provider_profiles,
    provider_env_file,
)

from .catalog import Catalog
from .paths import resolve_profile_root
from .readers import (
    artifact_path,
    read_artifacts,
    read_digest,
    read_evaluation,
    read_events,
    read_manifest,
    read_verdict,
    relative_run_path,
    run_from_manifest,
    runtime_from_manifest,
    trace_dir,
)

_HOST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    # A Windows drive path, in either separator style.
    r"[A-Za-z]:[\\/][^\"\'<>\r\n]*"
    r"|"
    # A UNC share.
    r"\\\\[^\"\'<>\r\n]+"
    r"|"
    # A POSIX absolute path that names a home or a known container root. Run
    # traces carry paths like /home/runner/.codex/... and /root/.config/...,
    # which the Windows-only patterns used to pass straight through to the
    # browser even though they name the host just as plainly.
    r"/(?:home|root|Users|mnt|opt|srv)/[^\"\'<>\s]*"
    r")"
)


def _strip_embedded_host_paths(value: str) -> str:
    return _HOST_PATH_RE.sub("<host-path>", value)


def _relative_profile_path(repo_root: Path, value: Any) -> str:
    """A profile file's location as a repository-relative label.

    The loader records an absolute path because it needs one; the browser does
    not, and must not receive one. A file outside the repository is reported by
    name only, since any correct answer would be a host path.
    """
    if not isinstance(value, str) or not value:
        return "profiles/providers/*.yaml"
    try:
        return Path(value).resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return Path(value).name


def _safe_value(value: Any, *, root: Path | None = None, key: str = "") -> Any:
    """Remove host paths and secrets before a value reaches the browser."""
    lower_key = key.lower()
    path_keys = {
        "run_dir",
        "gateway_env_file",
        "trace_dir",
        "log_path",
        "last_message_path",
        "output_path",
        "selected_host_path",
        "game_engine_root",
        "dsh_root",
        "runs_root",
        "output_dir",
        "path",
    }
    if isinstance(value, Mapping):
        return {
            str(name): _safe_value(item, root=root, key=str(name))
            for name, item in value.items()
            if not any(
                token in str(name).lower()
                for token in ("key", "token", "secret", "password", "authorization")
            )
        }
    if isinstance(value, list):
        return [_safe_value(item, root=root, key=key) for item in value]
    if isinstance(value, str):
        value = _strip_embedded_host_paths(value)
        if lower_key in path_keys:
            if value.startswith("/workspace/") or value == "/workspace":
                return value
            try:
                candidate = Path(value)
                if candidate.is_absolute() and root is not None:
                    return candidate.resolve().relative_to(root.resolve()).as_posix()
            except (OSError, ValueError):
                pass
            if Path(value).is_absolute():
                return "<host path omitted>"
        if len(value) > 4000:
            return value[:4000] + "…"
    return value


def _public_ref(value: Any, run_dir: Path) -> str:
    if not isinstance(value, str):
        return str(value)
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    try:
        return candidate.resolve().relative_to(run_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return "<host path omitted>"


def _score(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _check_summary(check: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    evaluator = str(check.get("evaluator") or "")
    details = check.get("details") if isinstance(check.get("details"), Mapping) else {}
    return {
        "check_id": check.get("check_id") or check.get("id"),
        "phase": check.get("phase"),
        "evaluator": evaluator,
        "annotator_kind": check.get("annotator_kind")
        or ("llm" if evaluator.startswith("agent.") else "code"),
        "status": check.get("status"),
        "passed": check.get("passed"),
        "score": _score(check.get("score")),
        "weight": check.get("weight"),
        "required": check.get("required"),
        "depends_on": check.get("depends_on") or [],
        "explanation": check.get("explanation") or details.get("explanation"),
        "details": _safe_value(details, root=run_dir),
        "evidence_refs": [_public_ref(ref, run_dir) for ref in (check.get("evidence_refs") or [])],
        "evaluator_run_id": check.get("evaluator_run_id"),
        "started_at": check.get("started_at"),
        "finished_at": check.get("finished_at"),
        "error": check.get("error"),
    }


def _find_evaluator_runs(run_dir: Path, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root = trace_dir(run_dir) / "evaluators"
    result: list[dict[str, Any]] = []
    if not root.is_dir():
        return result
    for evaluator_dir in sorted(path for path in root.glob("*/*") if path.is_dir()):
        evaluator_id = evaluator_dir.name
        result.append(
            {
                "evaluator_run_id": evaluator_id,
                "role": evaluator_dir.parent.name,
                "trace_dir": relative_run_path(run_dir, evaluator_dir),
                "events_path": relative_run_path(
                    run_dir, evaluator_dir / "normalized-events.jsonl"
                ),
                "result_files": [
                    relative_run_path(run_dir, path)
                    for path in evaluator_dir.glob("*-result.json")
                    if path.is_file()
                ],
                "check_ids": [
                    check.get("check_id")
                    for check in checks
                    if check.get("evaluator_run_id") == evaluator_id
                ],
            }
        )
    return result


def run_detail(catalog: Catalog, run_id: str) -> dict[str, Any] | None:
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    run_dir = Path(summary["run_dir"])
    manifest = read_manifest(run_dir) or {}
    public_summary = {key: value for key, value in summary.items() if key != "run_dir"}
    run = run_from_manifest(manifest)
    runtime = runtime_from_manifest(manifest)
    evaluation = read_evaluation(run_dir, manifest) or read_verdict(run_dir, manifest) or {}
    digest = read_digest(run_dir, manifest) or {}
    checks = [
        _check_summary(check, run_dir)
        for check in (evaluation.get("checks") or [])
        if isinstance(check, Mapping)
    ]
    evaluator_runs = _find_evaluator_runs(run_dir, checks)
    for check in checks:
        if check.get("evaluator_run_id"):
            continue
        check_id = str(check.get("check_id") or "")
        match = next(
            (item for item in evaluator_runs if str(item.get("role", "")).endswith(check_id)),
            None,
        )
        if match:
            check["evaluator_run_id"] = match["evaluator_run_id"]
            match.setdefault("check_ids", []).append(check_id)
    stats = digest.get("stats") if isinstance(digest.get("stats"), Mapping) else {}
    event_items = stats.get("event_items")
    if isinstance(event_items, Mapping):
        event_count = sum(int(value or 0) for value in event_items.values())
    elif isinstance(event_items, (int, float)):
        event_count = int(event_items)
    else:
        event_count = 0
    return {
        "summary": public_summary,
        "configuration": {
            "task_id": run.get("task_id"),
            "agent": run.get("agent"),
            "agent_profile": _safe_value(run.get("agent_profile") or {}, root=run_dir),
            "model": run.get("model"),
            "model_profile": run.get("model_profile"),
            "model_provider": run.get("model_provider"),
            "protocol": run.get("protocol"),
            "reasoning_effort": run.get("reasoning_effort"),
            "mcp_profile": run.get("mcp_profile"),
            "sandbox_profile": run.get("sandbox_profile"),
            "sandbox": _safe_value(run.get("sandbox") or {}),
            "snapshot_mode": run.get("snapshot_mode"),
            "snapshots": _safe_value(manifest.get("snapshots") or {}, root=run_dir),
            "runtime": _safe_value(runtime, root=run_dir),
        },
        "scores": {
            "outcome": _score(evaluation.get("outcome_score")),
            "quality": _score(evaluation.get("quality_score")),
            "process": _score(evaluation.get("process_score")),
        },
        "decision": evaluation.get("decision"),
        "checks": checks,
        "trace_summary": {
            "events": event_count,
            "tool_calls": stats.get("tool_calls_total"),
            "failed_tool_calls": stats.get("tool_calls_failed"),
            "commands": stats.get("shell_commands_total"),
            "failed_commands": stats.get("shell_commands_failed"),
            "errors": stats.get("errors"),
            "mcp_success_rate": stats.get("mcp_success_rate"),
            "last_agent_message": digest.get("last_agent_message"),
        },
        "artifacts": read_artifacts(run_dir),
        "evaluator_runs": evaluator_runs,
        "workspace": {
            "relative_root": "workspace",
            "sections": [
                {"id": "output", "label": "Outputs", "relative_path": "output"},
                {"id": "evidence", "label": "Evidence", "relative_path": "evidence"},
                {"id": "trace", "label": "Trace", "relative_path": "trace"},
                {"id": "agent-config", "label": "Agent Config", "relative_path": "agent-config"},
            ],
        },
        "references": {
            "manifest": relative_run_path(run_dir, run_dir / "run-manifest.json"),
            "evaluation": relative_run_path(
                run_dir, run_dir / "workspace" / "evidence" / "evaluation.json"
            ),
            "events": relative_run_path(
                run_dir, trace_dir(run_dir, manifest) / "normalized-events.jsonl"
            ),
            "digest": relative_run_path(run_dir, trace_dir(run_dir, manifest) / "digest.json"),
        },
    }


def run_evaluation(catalog: Catalog, run_id: str) -> dict[str, Any] | None:
    """Return the sanitized persisted evaluation report."""
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    run_dir = Path(summary["run_dir"])
    manifest = read_manifest(run_dir) or {}
    evaluation = read_evaluation(run_dir, manifest) or read_verdict(run_dir, manifest)
    return _safe_value(evaluation, root=run_dir) if evaluation else None


def run_digest(catalog: Catalog, run_id: str) -> dict[str, Any] | None:
    """Return the sanitized persisted trace digest."""
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    run_dir = Path(summary["run_dir"])
    manifest = read_manifest(run_dir) or {}
    digest = read_digest(run_dir, manifest)
    return _safe_value(digest, root=run_dir) if digest else None


def run_events(catalog: Catalog, run_id: str, **filters: Any) -> dict[str, Any] | None:
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    run_dir = Path(summary["run_dir"])
    manifest = read_manifest(run_dir) or {}
    events, has_more, last_seq = read_events(run_dir, manifest, **filters)
    return {
        "run_id": run_id,
        # Event payloads are the Agent's own tool calls, so they are full of host
        # paths from the trace. The other read models pass through `_safe_value`
        # and this one did not, which is how `D:\...\workspace\output\scene.blend`
        # reached the browser.
        "events": _safe_value(events, root=run_dir),
        "has_more": has_more,
        "next_after_seq": last_seq if events else filters.get("after_seq", -1),
    }


def evaluator_events(
    catalog: Catalog, run_id: str, evaluator_run_id: str, **filters: Any
) -> dict[str, Any] | None:
    """Read an evaluator child trace without treating it as a top-level Run."""
    if Path(evaluator_run_id).name != evaluator_run_id:
        return None
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    parent_dir = Path(summary["run_dir"])
    evaluator_root = trace_dir(parent_dir) / "evaluators"
    matches = [path for path in evaluator_root.glob(f"*/{evaluator_run_id}") if path.is_dir()]
    if not matches:
        return None
    events, has_more, last_seq = read_events(
        matches[0],
        events_file=matches[0] / "normalized-events.jsonl",
        event_run_id=evaluator_run_id,
        **filters,
    )
    return {
        "run_id": run_id,
        "evaluator_run_id": evaluator_run_id,
        "events": events,
        "has_more": has_more,
        "next_after_seq": last_seq,
    }


def artifact_content(
    catalog: Catalog, run_id: str, artifact_id: str
) -> tuple[dict[str, Any], Path] | None:
    summary = catalog.get_run(run_id, include_internal=True)
    if not summary:
        return None
    return artifact_path(Path(summary["run_dir"]), artifact_id)


#: Fingerprint fields that a design may legitimately vary. A comparison that
#: varies `agent` is comparing Agents; a matrix that varies `reasoning_effort` is
#: measuring that axis. Either way the varying field is the question, not a
#: broken invariant, so it is excluded from the fairness fingerprint.
_FINGERPRINT_FIELDS = (
    "task_id",
    "task_bundle",
    "test_plan",
    "model_profile",
    "model",
    "model_provider",
    "provider",
    "provider_env_file",
    "reasoning_effort",
    "mcp_profile",
    "mcp_servers",
    "sandbox_profile",
    "sandbox",
    "resource_specs",
)


def _comparison_fingerprint(
    manifest: Mapping[str, Any], *, varying: Collection[str] = ()
) -> str:
    """A digest of the inputs a comparison held fixed.

    ``varying`` names the axes the design deliberately changes. Omitting them is
    what makes the fingerprint mean "these runs shared every controlled input"
    rather than "these runs were identical" -- the latter is false by
    construction for any experiment worth running, and reporting it as an
    unfairness would flag every well-formed sweep.
    """
    run = run_from_manifest(manifest)
    value = {
        field: run.get(field)
        for field in _FINGERPRINT_FIELDS
        if field not in varying
    }
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def comparison_detail(catalog: Catalog, comparison_id: str) -> dict[str, Any] | None:
    payload = catalog.get_comparison(comparison_id)
    if not payload:
        return None
    raw_invariant = payload.get("invariant")
    raw_invariant = raw_invariant if isinstance(raw_invariant, Mapping) else {}
    # The design records which axes it changes. Two runs that differ only in a
    # declared axis are the experiment working, not a broken invariant.
    # `agent` is always varying: comparing Agents is the point of `compare`.
    raw_vary = raw_invariant.get("vary")
    varying = {"agent"}
    if isinstance(raw_vary, Mapping):
        varying |= {str(key) for key in raw_vary}
    runs = []
    check_matrix: dict[str, dict[str, Any]] = {}
    fingerprints: list[str] = []
    for item in payload.get("runs") or []:
        if not isinstance(item, Mapping):
            continue
        run_id = str(item.get("run_id") or "")
        internal_summary = catalog.get_run(run_id, include_internal=True) if run_id else None
        summary = (
            {key: value for key, value in internal_summary.items() if key != "run_dir"}
            if internal_summary
            else None
        )
        participant_root = Path(internal_summary["run_dir"]) if internal_summary else None
        participant = {
            key: _safe_value(item.get(key), root=participant_root)
            for key in ("run_id", "agent", "model", "status", "evaluation", "error")
            if key in item
        }
        runs.append({**participant, "summary": summary})
        if internal_summary:
            run_manifest = read_manifest(Path(internal_summary["run_dir"]))
            if run_manifest:
                fingerprints.append(_comparison_fingerprint(run_manifest, varying=varying))
            detail = run_detail(catalog, run_id)
            for check in (detail or {}).get("checks", []):
                check_id = str(check.get("check_id") or "")
                if not check_id:
                    continue
                entry = check_matrix.setdefault(
                    check_id,
                    {"check_id": check_id, "phase": check.get("phase"), "values": []},
                )
                entry["values"].append(
                    {
                        "run_id": run_id,
                        "agent": summary.get("agent_id") if summary else None,
                        "status": check.get("status"),
                        "score": check.get("score"),
                    }
                )
    invariant = payload.get("invariant") if isinstance(payload.get("invariant"), Mapping) else {}
    fair = bool(invariant) and bool(fingerprints) and len(set(fingerprints)) == 1
    if not invariant:
        fairness_message = "Invariant metadata is missing."
    elif not fingerprints:
        fairness_message = "Run manifests are unavailable; fairness cannot be verified."
    elif fair:
        fairness_message = (
            "Task, TestPlan, resources, Sandbox, MCP, Model and Evaluator inputs match."
        )
    else:
        fairness_message = "Run manifests differ in one or more fixed inputs; compare with caution."
    return {
        "comparison_id": payload.get("comparison_id") or comparison_id,
        "created_at": payload.get("created_at"),
        "invariant": _safe_value(invariant),
        "runs": runs,
        "check_matrix": list(check_matrix.values()),
        "output_dir": None,
        "fairness": {"fair": fair, "message": fairness_message},
    }


def _list_yaml_profiles(root: Path, kind: str) -> list[dict[str, Any]]:
    result = []
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.yaml")):
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            value = {}
        data = value if isinstance(value, Mapping) else {}
        profile_id = str(data.get("id") or path.stem)
        # A profile may carry a human label; without one the dropdown would show
        # a bare id like `codex-dcc`, which says nothing about when to use it.
        label = str(data.get("label") or "").strip()
        description = str(data.get("description") or "").strip()
        entry = {
            "id": profile_id,
            "kind": kind,
            "path": path.name,
            "label": label or profile_id,
            "summary": description or str(data.get("adapter") or data.get("model") or ""),
            "fields": sorted(
                str(key)
                for key in data.keys()
                if str(key).lower() not in {"environment", "env", "api_key", "token"}
            ),
        }
        if kind == "agent":
            # The version is what an operator comparing Agents needs, and it is
            # the Agent's own property. Host capabilities are not repeated here:
            # they come from the run's MCP profile, and declaring them twice is
            # how the two came to disagree.
            entry["workdir"] = str(data.get("workdir") or "")
            entry["agent_version"] = str(data.get("agent_version") or "")
            # Exposed because it decides which versions this profile can run.
            build = data.get("build")
            entry["build"] = dict(build) if isinstance(build, Mapping) else {}
            entry["image"] = str(data.get("image") or "")
            if not entry["image"]:
                repository = str(data.get("image_repository") or "")
                if repository and entry["agent_version"]:
                    entry["image"] = f"{repository}:{entry['agent_version']}"
            entry["available_versions"] = _agent_versions(entry)
        if kind == "model":
            entry["provider_id"] = str(data.get("provider") or "default")
            entry["model_id"] = str(data.get("model") or entry["id"])
            entry["label"] = entry["model_id"]
        result.append(entry)
    return result


def _agent_versions(entry: dict[str, Any]) -> list[str]:
    """Versions of this Agent that are built, so a choice can actually start.

    Read from the images rather than from a list in a file: a version nobody
    built cannot start, and a hand-kept list drifts the moment someone builds or
    prunes one.

    An earlier version also filtered by build kind, for the case of two profiles
    sharing an image repository while launching it differently. No two profiles
    share a repository now, so the filter had nothing left to decide; if that
    changes, the profiles need a way to say which tags are theirs, and the build
    kind is not it -- the kind described packaging, not identity.
    """
    from ai_native_evals.runs.images import available_versions

    image = str(entry.get("image") or "")
    repository = image.rpartition(":")[0] if ":" in image else ""
    if not repository or "/" in image.rpartition(":")[2]:
        return [str(entry["agent_version"])] if entry.get("agent_version") else []
    return available_versions(repository)


def registry(repo_root: Path) -> dict[str, Any]:
    tasks = []
    task_root = repo_root / "tasks"
    if task_root.is_dir():
        for directory in sorted(task_root.iterdir()):
            task_file = directory / "task.yaml"
            if not directory.is_dir() or not task_file.is_file():
                continue
            try:
                value = yaml.safe_load(task_file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                value = {}
            data = value if isinstance(value, Mapping) else {}
            test_plan = data.get("test_plan") if isinstance(data.get("test_plan"), Mapping) else {}
            checks = test_plan.get("checks") if isinstance(test_plan.get("checks"), list) else []
            execution = data.get("execution") if isinstance(data.get("execution"), Mapping) else {}
            tasks.append(
                {
                    "id": str(data.get("id") or directory.name),
                    "kind": "task",
                    "path": str(task_file.relative_to(repo_root)).replace("\\", "/"),
                    "summary": str(data.get("description") or "Task Bundle"),
                    "checks": len(checks),
                    # What the Task says it needs to run. A Task that drives
                    # Blender declares `mcp_profile: blender-host`; without this
                    # the Console could only preselect the global default, which
                    # silently overrode the Task's own requirement and started
                    # the run with no MCP tools at all.
                    "execution": {
                        str(key): str(item)
                        for key, item in execution.items()
                        if isinstance(item, str) and item
                    },
                }
            )
    from .paths import load_eval_config

    config = load_eval_config(repo_root)
    raw_defaults = config.get("defaults") if isinstance(config.get("defaults"), Mapping) else {}
    models = _list_yaml_profiles(resolve_profile_root(repo_root, "models"), "model")
    # A provider is an upstream, not a grouping label for model bindings.
    # Bindings remain listed separately so an old run's binding stays findable.
    provider_map: dict[str, dict[str, Any]] = {}
    try:
        provider_profiles = load_provider_profiles(repo_root, config)
    except ProviderError:
        provider_profiles = {}
    for provider_id, profile in provider_profiles.items():
        env_path = provider_env_file(repo_root, profile)
        declared = profile.get("models") if isinstance(profile.get("models"), list) else []
        provider_map[provider_id] = {
            "id": provider_id,
            "kind": "provider",
            # A repository-relative name, never the absolute path the loader
            # records: `source_path` is where this machine keeps the file, and the
            # Console's contract is that it does not hand the browser host paths.
            "path": _relative_profile_path(repo_root, profile.get("source_path")),
            "summary": (
                ("已配置" if env_path and env_path.is_file() else "未配置")
                + " · 上游 "
                + str(profile.get("name") or provider_id)
            ),
            "model_ids": [
                str(entry.get("id"))
                for entry in declared
                if isinstance(entry, Mapping) and entry.get("id")
            ],
            "model_profiles": [],
        }
    default_model_profile = raw_defaults.get("model_profile")
    default_model = next(
        (item for item in models if item.get("id") == default_model_profile),
        None,
    )
    defaults = {
        key: raw_defaults.get(key)
        for key in (
            "agent",
            "evaluator_agent",
            # The form preselects from these; omitting them left the model field
            # empty and the operator learned it was required only at submit.
            "provider",
            "model",
            "reasoning_effort",
            "model_profile",
            "mcp_profile",
            "sandbox_profile",
            "snapshot_mode",
        )
        if raw_defaults.get(key) is not None
    }
    if default_model:
        defaults["model_provider"] = default_model.get("provider_id")
        defaults["model"] = default_model.get("model_id")
    return {
        "tasks": tasks,
        "agents": _list_yaml_profiles(resolve_profile_root(repo_root, "agents"), "agent"),
        "providers": list(provider_map.values()),
        "models": models,
        "mcp": _list_yaml_profiles(resolve_profile_root(repo_root, "mcp"), "mcp"),
        "sandboxes": _list_yaml_profiles(resolve_profile_root(repo_root, "sandboxes"), "sandbox"),
        "presets": _list_yaml_profiles(repo_root / "config" / "presets", "preset"),
        "defaults": defaults,
    }
