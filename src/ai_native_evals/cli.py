"""CLI for Inspect diagnostics and manual evaluation run lifecycle."""

from __future__ import annotations

import argparse
import json
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
    )
    run_dir = prepare_run(
        repo_root,
        spec,
        game_engine_ref=args.game_engine_ref,
        dsh_ref=args.dsh_ref,
    )
    return repo_root, run_dir


def _prepare(args: argparse.Namespace) -> int:
    _repo_root_value, run_dir = _prepare_from_args(args)
    print(json.dumps(load_manifest(run_dir), ensure_ascii=False, indent=2))
    return 0


def _execute(args: argparse.Namespace) -> int:
    repo_root, run_dir = _prepare_from_args(args)
    start_docker_run(run_dir, repo_root)
    result = wait_docker_run(run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
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


def main() -> int:
    """Run diagnostics or manual evaluation lifecycle commands."""
    parser = argparse.ArgumentParser(description="AI-Native Agent evaluation controls")
    parser.add_argument("--version", action="store_true", help="print the package version")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="manual evaluation run lifecycle")
    run_subparsers = run_parser.add_subparsers(dest="run_command", required=True)

    prepare_parser = run_subparsers.add_parser("prepare", help="snapshot repos and create a run")
    prepare_parser.add_argument("task_id")
    _add_run_options(prepare_parser)

    execute_parser = run_subparsers.add_parser(
        "execute", help="prepare a snapshot, run the Docker Agent, and wait for completion"
    )
    execute_parser.add_argument("task_id")
    _add_run_options(execute_parser)

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
        if args.command != "run":
            parser.print_help()
            return 0
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
