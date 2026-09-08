"""CLI for Inspect diagnostics and manual evaluation run lifecycle."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .runs import (
    DockerRuntimeError,
    EvalConfigError,
    RunLifecycleError,
    cleanup_run,
    load_manifest,
    prepare_run,
    read_docker_logs,
    resolve_run,
    set_status,
    start_docker_run,
    stop_docker_run,
    wait_docker_run,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_dir_from_arg(repo_root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / ".." / "EvalRuns" / candidate
    return candidate.resolve()


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="evaluation YAML config")
    parser.add_argument("--agent", help="Agent id override (codex or dsh)")
    parser.add_argument("--model-profile", help="model profile override")
    parser.add_argument("--game-engine-ref", help="Git ref for the Game Engine snapshot")
    parser.add_argument("--dsh-ref", help="Git ref for the AI-Native-DSH snapshot")
    parser.add_argument("--mcp-profile", help="MCP profile override")
    parser.add_argument("--sandbox-profile", help="Sandbox profile override")
    parser.add_argument("--preset", help="named run preset (agent/model/MCP/sandbox selectors)")


def _prepare_from_args(args: argparse.Namespace) -> tuple[Path, Path]:
    repo_root = _repo_root()
    config = args.config.resolve() if args.config else repo_root / "config" / "eval.yaml"
    spec = resolve_run(
        repo_root,
        args.task_id,
        config_path=config,
        agent=args.agent,
        model_profile=args.model_profile,
        game_engine_ref=args.game_engine_ref,
        dsh_ref=args.dsh_ref,
        mcp_profile=args.mcp_profile,
        sandbox_profile=args.sandbox_profile,
        preset=args.preset,
    )
    run_dir = prepare_run(
        repo_root,
        spec,
        game_engine_ref=args.game_engine_ref,
        dsh_ref=args.dsh_ref,
    )
    return repo_root, run_dir


def _plan(args: argparse.Namespace) -> int:
    """Resolve and display one task's declarative test plan."""
    repo_root = _repo_root()
    config = args.config.resolve() if args.config else repo_root / "config" / "eval.yaml"
    spec = resolve_run(
        repo_root,
        args.task_id,
        config_path=config,
        agent=args.agent,
        model_profile=args.model_profile,
        game_engine_ref=args.game_engine_ref,
        dsh_ref=args.dsh_ref,
        mcp_profile=args.mcp_profile,
        sandbox_profile=args.sandbox_profile,
        preset=args.preset,
    )
    payload = {
        "task_id": spec.task_id,
        "test_plan": spec.test_plan.to_dict(),
        "ordered_checks": [check.id for check in spec.test_plan.ordered_checks()],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _prepare(args: argparse.Namespace) -> int:
    _repo_root_value, run_dir = _prepare_from_args(args)
    print(json.dumps(load_manifest(run_dir), ensure_ascii=False, indent=2))
    return 0


def _execute(args: argparse.Namespace) -> int:
    repo_root, run_dir = _prepare_from_args(args)
    start_docker_run(run_dir, repo_root)
    result = wait_docker_run(run_dir)
    evaluation = None
    if not args.no_evaluate and result.get("status") in {"completed", "failed"}:
        plan = result.get("run", {}).get("test_plan", {})
        if isinstance(plan, dict) and plan.get("checks"):
            from .evaluation.runner import EvaluationError, evaluate_run

            try:
                evaluation = evaluate_run(run_dir, repo_root=repo_root)
            except (EvaluationError, OSError, ValueError) as exc:
                evaluation = {"decision": "not_evaluable", "error": str(exc)}
    payload = {"run": result, "evaluation": evaluation}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.get("status") != "completed":
        return 1
    if evaluation is not None and evaluation.get("decision") != "pass":
        return 1
    return 0


def _status(args: argparse.Namespace) -> int:
    manifest = load_manifest(_run_dir_from_arg(_repo_root(), args.run_id))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _start(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    run_dir = _run_dir_from_arg(repo_root, args.run_id)
    print(json.dumps(start_docker_run(run_dir, repo_root), ensure_ascii=False, indent=2))
    return 0


def _wait(args: argparse.Namespace) -> int:
    run_dir = _run_dir_from_arg(_repo_root(), args.run_id)
    print(json.dumps(wait_docker_run(run_dir), ensure_ascii=False, indent=2))
    return 0


def _logs(args: argparse.Namespace) -> int:
    run_dir = _run_dir_from_arg(_repo_root(), args.run_id)
    result = read_docker_logs(run_dir, tail=args.tail)
    print(result["logs"], end="" if result["logs"].endswith("\n") else "\n")
    return 0


def _stop(args: argparse.Namespace) -> int:
    run_dir = _run_dir_from_arg(_repo_root(), args.run_id)
    manifest = load_manifest(run_dir)
    if manifest.get("runtime"):
        result = stop_docker_run(run_dir)
    else:
        result = set_status(run_dir, "stopped")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    run_dir = _run_dir_from_arg(repo_root, args.run_id)
    manifest = load_manifest(run_dir)
    if manifest.get("runtime", {}).get("status") not in (None, "stopped"):
        stop_docker_run(run_dir)
        manifest = load_manifest(run_dir)
    runs_root = Path(manifest["run"]["runs_root"])
    cleanup_run(run_dir, runs_root)
    print(f"cleaned {run_dir}")
    return 0





def _evaluate(args: argparse.Namespace) -> int:
    """Execute the prepared run's declarative TestPlan."""
    from .evaluation.runner import EvaluationError, evaluate_run

    run_dir = _run_dir_from_arg(_repo_root(), args.run_id)
    try:
        report = evaluate_run(run_dir, repo_root=_repo_root())
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("decision") == "pass" else 1


def _verify(args: argparse.Namespace) -> int:
    """Run independent host read-back verification against a finished run dir.

    Expectations come from the same task manifest that produced the run, so the
    verify gate can never drift from the task definition. Verification only
    ever happens on the host, never through the container-facing mcp_host.
    """
    from .scorers.multi_dcc_host_verifier import verify_host_state

    manifest = load_manifest(_run_dir_from_arg(_repo_root(), args.run_id))
    run = manifest["run"]
    verify_config = run.get("verify") or {}
    if not verify_config:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": "run manifest has no task verify expectations; cannot verify",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    evidence_dir = Path(manifest["paths"]["evidence"])

    blender_expectations = verify_config.get("blender_objects") or []
    ue5_expectations = verify_config.get("ue5") or {}
    scene_name = verify_config.get("blender_scene", "roundtrip.blend")

    # The scorer keys off its own hardcoded filename; hand it an explicit file
    # expectation through a temp-normalized evidence dir is not clean, so we
    # copy the scene expectation into an env var consumed by verify_host_state.
    import os as _os

    _os.environ["AI_NATIVE_EVALS_BLEND_SCENE_NAME"] = str(scene_name)

    passed, findings = verify_host_state(
        evidence_dir=evidence_dir,
        expected_blend_objects=blender_expectations,
        expected_ue5=ue5_expectations,
        mcp_url="http://127.0.0.1:8000/mcp",
        skip_if_absent=False,
    )
    print(json.dumps({"passed": passed, "findings": findings}, ensure_ascii=False, indent=2))
    return 0 if passed else 1




def _digest(args: argparse.Namespace) -> int:
    """Render a per-run digest and persist digest.json + digest.md in trace/."""
    from .adapters.run_digest import (
        DigestError,
        render_digest_markdown,
        write_digest_files,
    )

    run_dir = _run_dir_from_arg(_repo_root(), args.run_id)
    try:
        digest = write_digest_files(run_dir)
    except DigestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(digest, ensure_ascii=False, indent=2))
    else:
        print(render_digest_markdown(digest))
    return 0




def _summary(args: argparse.Namespace) -> int:
    """Render a history table across all runs (one row per run)."""
    from .adapters.run_digest import render_summary_markdown, summarize_runs

    runs_root = _repo_root() / ".." / "EvalRuns"
    rows = summarize_runs(runs_root)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_summary_markdown(rows))
    return 0



def _config_for_repo(repo_root: Path, value: Path | None = None) -> tuple[Path, dict[str, object]]:
    path = value.resolve() if value else repo_root / "config" / "eval.yaml"
    from .runs.resolver import load_config

    return path, load_config(path)


def _task_list(args: argparse.Namespace) -> int:
    from .tasks.bundles import list_task_bundles

    repo_root = _repo_root()
    _path, config = _config_for_repo(repo_root, args.config)
    payload = {"tasks": list(list_task_bundles(repo_root, config=config))}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _task_new(args: argparse.Namespace) -> int:
    """Create a minimal one-file Task Bundle without overwriting by default."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.task_id):
        raise EvalConfigError(
            "task id must start with a lowercase letter/digit and contain only "
            "lowercase letters, digits, '-' or '_'"
        )
    task_dir = _repo_root() / "tasks" / args.task_id
    task_file = task_dir / "task.yaml"
    if task_file.exists() and not args.force:
        raise EvalConfigError(f"task already exists: {task_file}; use --force to replace task.yaml")
    task_dir.mkdir(parents=True, exist_ok=True)
    template = f"""id: {args.task_id}
version: 1

# Keep this inline for a small task. Move it to prompt.md when it grows.
prompt: |
  Describe the work for task {args.task_id} here.
  Write the required artifact under /workspace/output and read it back before finishing.

# Add repository/fixture/host_service entries only when this Task needs them.
resources: []

# execution contains selectors only; use --agent/--model-profile for comparisons.
execution:
  mcp_profile: none

test_plan:
  version: 1
  checks:
    - id: result-file
      phase: outcome
      evaluator: script.file_exists.v1
      input:
        path: /workspace/output/result.json
      required: true
      on_error: fail
"""
    task_file.write_text(template, encoding="utf-8")
    print(
        json.dumps(
            {"created": str(task_file), "task_id": args.task_id},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _task_show(args: argparse.Namespace) -> int:
    from .tasks.bundles import load_task_bundle

    repo_root = _repo_root()
    _path, config = _config_for_repo(repo_root, args.config)
    bundle = load_task_bundle(repo_root, args.task_id, config=config)
    print(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _task_validate(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    config_path, _config = _config_for_repo(repo_root, args.config)
    spec = resolve_run(repo_root, args.task_id, config_path=config_path)
    from .evaluation.runner import available_evaluators

    unknown_evaluators = sorted(
        {check.evaluator for check in spec.test_plan.checks} - set(available_evaluators())
    )
    if unknown_evaluators:
        raise EvalConfigError(f"unknown evaluator ids: {unknown_evaluators}")
    missing_resources = [
        resource.resource_id
        for resource in spec.resource_specs
        if resource.required and resource.source is not None and not resource.source.exists()
    ]
    if missing_resources:
        raise EvalConfigError(f"required resource sources do not exist: {missing_resources}")
    payload = {
        "valid": True,
        "task_id": spec.task_id,
        "bundle": spec.task_bundle.get("source_path"),
        "agent": spec.agent,
        "model": spec.model,
        "checks": [check.id for check in spec.test_plan.ordered_checks()],
        "resources": [
            {
                **resource.to_dict(),
                "source_exists": bool(resource.source and resource.source.exists()),
            }
            for resource in spec.resource_specs
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _profile_catalog(repo_root: Path, kind: str) -> dict[str, dict[str, object]]:
    _config_path, config = _config_for_repo(repo_root)
    from .runs.resolver import _mapping, _merge_named_profile_source

    profiles_config = _mapping(config, "profiles")
    mapping = {
        "agents": ("agents", "agents", "profiles/agents"),
        "models": ("model_profiles", "models", "profiles/models"),
        "mcp": ("mcp_profiles", "mcp", "profiles/mcp"),
        "sandboxes": ("sandbox_profiles", "sandboxes", "profiles/sandboxes"),
        "presets": ("presets", "presets", "config/presets"),
    }
    if kind not in mapping:
        raise EvalConfigError(f"unsupported profile catalog: {kind}")
    inline_key, profile_key, default_root = mapping[kind]
    return _merge_named_profile_source(
        repo_root,
        config,
        inline_key,
        {**_mapping(profiles_config, profile_key), **_mapping(config, inline_key)},
        profile_root_key=profile_key,
        default_root=default_root,
    )


def _agent_list(_args: argparse.Namespace) -> int:
    profiles = _profile_catalog(_repo_root(), "agents")
    print(json.dumps({"agents": profiles}, ensure_ascii=False, indent=2))
    return 0


def _model_list(_args: argparse.Namespace) -> int:
    profiles = _profile_catalog(_repo_root(), "models")
    print(json.dumps({"models": profiles}, ensure_ascii=False, indent=2))
    return 0


def _profile_list(args: argparse.Namespace) -> int:
    kind = args.profile_kind
    key = "sandboxes" if kind == "sandbox" else kind
    profiles = _profile_catalog(_repo_root(), key)
    print(json.dumps({key: profiles}, ensure_ascii=False, indent=2))
    return 0


def _compare(args: argparse.Namespace) -> int:
    """Run one Task through multiple Agents with shared evaluation inputs."""
    from .runs.compare import compare_task, render_comparison_markdown

    agents = tuple(value.strip() for value in args.agents.split(","))
    config_path = args.config.resolve() if args.config else None
    report = compare_task(
        _repo_root(),
        args.task_id,
        agents,
        config_path=config_path,
        model_profile=args.model_profile,
        mcp_profile=args.mcp_profile,
        sandbox_profile=args.sandbox_profile,
        preset=args.preset,
        game_engine_ref=args.game_engine_ref,
        dsh_ref=args.dsh_ref,
        evaluate=not args.no_evaluate,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_comparison_markdown(report))
    successful = all(
        run.get("status") == "completed"
        and (
            args.no_evaluate
            or (run.get("evaluation") or {}).get("decision") == "pass"
        )
        for run in report.get("runs", [])
    )
    return 0 if successful else 1


def _config_show(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    path, config = _config_for_repo(repo_root, args.config)
    print(json.dumps({"path": str(path), "config": config}, ensure_ascii=False, indent=2))
    return 0


def _doctor(_args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    checks: dict[str, object] = {}
    try:
        config_path, config = _config_for_repo(repo_root)
        checks["config"] = {"ok": True, "path": str(config_path)}
        from .tasks.bundles import list_task_bundles

        task_ids = list_task_bundles(repo_root, config=config)
        checks["tasks"] = {"ok": bool(task_ids), "count": len(task_ids)}
        agents = _profile_catalog(repo_root, "agents")
        models = _profile_catalog(repo_root, "models")
        checks["agents"] = {"ok": bool(agents), "ids": sorted(agents)}
        checks["models"] = {"ok": bool(models), "ids": sorted(models)}
        checks["inspect_ai"] = {"ok": True}
        checks["wsl"] = {"ok": shutil.which("wsl.exe") is not None}
    except (EvalConfigError, OSError, ValueError) as exc:
        checks["error"] = str(exc)
    ok = all(value.get("ok", False) for value in checks.values() if isinstance(value, dict))
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1

def main() -> int:
    """Run diagnostics or manual evaluation lifecycle commands."""
    parser = argparse.ArgumentParser(description="AI-Native Agent evaluation controls")
    parser.add_argument("--version", action="store_true", help="print the package version")
    subparsers = parser.add_subparsers(dest="command")

    compare_parser = subparsers.add_parser(
        "compare", help="run one Task through multiple Agent profiles"
    )
    compare_parser.add_argument("task_id")
    compare_parser.add_argument("--agents", required=True, help="comma-separated Agent ids")
    compare_parser.add_argument("--config", type=Path)
    compare_parser.add_argument("--preset")
    compare_parser.add_argument("--model-profile")
    compare_parser.add_argument("--mcp-profile")
    compare_parser.add_argument("--sandbox-profile")
    compare_parser.add_argument("--game-engine-ref")
    compare_parser.add_argument("--dsh-ref")
    compare_parser.add_argument("--no-evaluate", action="store_true")
    compare_parser.add_argument(
        "--json", action="store_true", help="print JSON instead of Markdown"
    )

    task_parser = subparsers.add_parser("task", help="discover and validate Task bundles")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)
    task_list_parser = task_subparsers.add_parser("list", help="list Task ids")
    task_list_parser.add_argument("--config", type=Path)
    task_show_parser = task_subparsers.add_parser("show", help="show a Task bundle")
    task_show_parser.add_argument("task_id")
    task_show_parser.add_argument("--config", type=Path)
    task_validate_parser = task_subparsers.add_parser(
        "validate", help="validate a Task and its TestPlan"
    )
    task_validate_parser.add_argument("task_id")
    task_validate_parser.add_argument("--config", type=Path)
    task_new_parser = task_subparsers.add_parser(
        "new", help="create a minimal one-file Task Bundle"
    )
    task_new_parser.add_argument("task_id")
    task_new_parser.add_argument("--force", action="store_true", help="replace only task.yaml")

    agent_parser = subparsers.add_parser("agent", help="list Agent profiles")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_subparsers.add_parser("list", help="list Agent profiles")

    model_parser = subparsers.add_parser("model", help="list model profiles")
    model_subparsers = model_parser.add_subparsers(dest="model_command", required=True)
    model_subparsers.add_parser("list", help="list model profiles")

    for command_name, profile_kind in (
        ("mcp", "mcp"),
        ("sandbox", "sandbox"),
        ("preset", "presets"),
    ):
        profile_parser = subparsers.add_parser(
            command_name, help=f"list {command_name} profiles"
        )
        profile_subparsers = profile_parser.add_subparsers(
            dest=f"{command_name}_command", required=True
        )
        list_parser = profile_subparsers.add_parser("list", help=f"list {command_name} profiles")
        list_parser.set_defaults(profile_kind=profile_kind)

    config_parser = subparsers.add_parser("config", help="inspect global evaluator configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="show resolved global config")
    config_show_parser.add_argument("--config", type=Path)

    subparsers.add_parser("doctor", help="check local evaluator wiring")

    run_parser = subparsers.add_parser("run", help="manual evaluation run lifecycle")
    run_subparsers = run_parser.add_subparsers(dest="run_command", required=True)

    plan_parser = run_subparsers.add_parser(
        "plan", help="resolve and display a task's declarative test plan"
    )
    plan_parser.add_argument("task_id")
    _add_run_options(plan_parser)

    prepare_parser = run_subparsers.add_parser("prepare", help="snapshot repos and create a run")
    prepare_parser.add_argument("task_id")
    _add_run_options(prepare_parser)

    execute_parser = run_subparsers.add_parser(
        "execute", help="prepare a snapshot, run the Docker Agent, and wait for completion"
    )
    execute_parser.add_argument("task_id")
    _add_run_options(execute_parser)
    execute_parser.add_argument(
        "--no-evaluate", action="store_true", help="stop after the subject Agent run"
    )

    status_parser = run_subparsers.add_parser("status", help="show a run manifest")
    status_parser.add_argument("run_id")

    start_parser = run_subparsers.add_parser(
        "start", help="start the WSL-backed Docker gateway and Agent"
    )
    start_parser.add_argument("run_id")

    wait_parser = run_subparsers.add_parser(
        "wait", help="wait for the Agent, save logs, and release Docker resources"
    )
    wait_parser.add_argument("run_id")

    logs_parser = run_subparsers.add_parser("logs", help="show recent Agent container logs")
    logs_parser.add_argument("run_id")
    logs_parser.add_argument("--tail", type=int, default=200)

    summary_parser = run_subparsers.add_parser(
        "summary", help="render a history table across all runs"
    )
    summary_parser.add_argument("--json", action="store_true", help="emit rows as JSON")
    digest_parser = run_subparsers.add_parser(
        "digest", help="render a per-run digest: what the agent did and how it struggled"
    )
    digest_parser.add_argument("run_id")
    digest_parser.add_argument("--json", action="store_true", help="emit raw digest JSON")
    evaluate_parser = run_subparsers.add_parser(
        "evaluate", help="execute a run's declarative TestPlan and write its verdict"
    )
    evaluate_parser.add_argument("run_id")

    verify_parser = run_subparsers.add_parser(
        "verify", help="run independent host read-back verification against a finished run"
    )
    verify_parser.add_argument("run_id")
    stop_parser = run_subparsers.add_parser("stop", help="stop the Docker Agent and gateway")
    stop_parser.add_argument("run_id")

    cleanup_parser = run_subparsers.add_parser("cleanup", help="remove a run workspace")
    cleanup_parser.add_argument("run_id")

    args = parser.parse_args()
    try:
        if args.version:
            print(__version__)
            return 0
        if args.command == "compare":
            return _compare(args)
        if args.command == "task":
            if args.task_command == "list":
                return _task_list(args)
            if args.task_command == "show":
                return _task_show(args)
            if args.task_command == "validate":
                return _task_validate(args)
            if args.task_command == "new":
                return _task_new(args)
        if args.command == "agent" and args.agent_command == "list":
            return _agent_list(args)
        if args.command == "model" and args.model_command == "list":
            return _model_list(args)
        if args.command in {"mcp", "sandbox", "preset"}:
            if getattr(args, f"{args.command}_command") == "list":
                return _profile_list(args)
        if args.command == "config" and args.config_command == "show":
            return _config_show(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command != "run":
            parser.print_help()
            return 0
        if args.run_command == "plan":
            return _plan(args)
        if args.run_command == "prepare":
            return _prepare(args)
        if args.run_command == "execute":
            return _execute(args)
        if args.run_command == "status":
            return _status(args)
        if args.run_command == "start":
            return _start(args)
        if args.run_command == "wait":
            return _wait(args)
        if args.run_command == "logs":
            return _logs(args)
        if args.run_command == "summary":
            return _summary(args)
        if args.run_command == "digest":
            return _digest(args)
        if args.run_command == "evaluate":
            return _evaluate(args)
        if args.run_command == "verify":
            return _verify(args)
        if args.run_command == "stop":
            return _stop(args)
        if args.run_command == "cleanup":
            return _cleanup(args)
        parser.error(f"unsupported run command: {args.run_command}")
    except (DockerRuntimeError, EvalConfigError, RunLifecycleError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
