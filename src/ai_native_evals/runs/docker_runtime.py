"""Docker runtime for isolated evaluation runs.

The runtime owns only the evaluation containers and network. The source project
is copied into a per-run snapshot before this module mounts it read-write into
an Agent container. Host Blender/UE5 processes stay outside the container and
are reached only through the configured MCP endpoint.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .lifecycle import load_manifest, update_manifest


class DockerRuntimeError(RuntimeError):
    """Raised when the WSL-backed Docker runtime cannot manage a run."""


_DEFAULT_WSL_DISTRO = "Ubuntu-20.04"
_DEFAULT_GATEWAY_IMAGE = "ai-native-llm-gateway:local"


def start_docker_run(
    run_dir: Path,
    repo_root: Path,
    *,
    wsl_distro: str | None = None,
    gateway_image: str | None = None,
    gateway_env_file: Path | None = None,
) -> dict[str, Any]:
    """Start one isolated network, gateway, and Agent container."""
    run_dir = run_dir.resolve()
    manifest = load_manifest(run_dir)
    if manifest.get("status") in {"running", "completed"}:
        raise DockerRuntimeError(f"run is already active or completed: {run_dir}")
    existing_runtime = manifest.get("runtime")
    if isinstance(existing_runtime, dict) and existing_runtime.get("status") not in (
        None,
        "stopped",
    ):
        raise DockerRuntimeError("run has live Docker resources; stop it before restarting")

    run = _run_metadata(manifest)
    sandbox = _sandbox_metadata(run)
    run_id = str(run["run_id"])
    runtime = _runtime_metadata(run_id)
    distro = wsl_distro or str(
        sandbox.get(
            "distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", _DEFAULT_WSL_DISTRO)
        )
    )
    gateway_image = gateway_image or os.environ.get(
        "AI_NATIVE_EVALS_GATEWAY_IMAGE", str(sandbox.get("gateway_image", _DEFAULT_GATEWAY_IMAGE))
    )
    env_file = (gateway_env_file or Path(
        os.environ.get("AI_NATIVE_EVALS_GATEWAY_ENV_FILE", str(repo_root / "config" / ".env.local"))
    )).resolve()

    if not env_file.is_file():
        raise DockerRuntimeError(
            f"gateway env file does not exist: {env_file}; "
            "create config/.env.local without committing it"
        )

    gateway_key = _gateway_key(run_id)
    _docker(distro, "network", "create", runtime["network"])
    try:
        _docker(
            distro,
            "run",
            "--detach",
            "--name",
            runtime["gateway_container"],
            "--network",
            runtime["network"],
            "--network-alias",
            "llm-gateway",
            *_container_security_args(sandbox),
            "--env-file",
            _linux_path(env_file),
            "--env",
            f"GATEWAY_API_KEY={gateway_key}",
            "--env",
            f"DEFAULT_MODEL={run['model']}",
            gateway_image,
        )
        _docker(
            distro,
            *_agent_run_args(
                manifest,
                runtime,
                gateway_key=gateway_key,
                image=str(run["agent_image"]),
            ),
        )
    except Exception as exc:
        _best_effort_stop(distro, runtime)
        if isinstance(exc, DockerRuntimeError):
            raise
        raise DockerRuntimeError(str(exc)) from exc

    runtime.update(
        {
            "wsl_distro": distro,
            "gateway_image": gateway_image,
            "gateway_env_file": str(env_file),
            "started_at": _utc_now(),
            "status": "running",
        }
    )
    return update_manifest(run_dir, status="running", runtime=runtime)


def wait_docker_run(run_dir: Path, *, wsl_distro: str | None = None) -> dict[str, Any]:
    """Wait for the Agent, persist its logs, and release Docker resources."""
    run_dir = run_dir.resolve()
    manifest = load_manifest(run_dir)
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("agent_container"):
        raise DockerRuntimeError("run has no Agent container")
    distro = wsl_distro or runtime.get(
        "wsl_distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", _DEFAULT_WSL_DISTRO)
    )

    exit_text = _docker(distro, "wait", str(runtime["agent_container"]))
    try:
        exit_code = int(exit_text.strip())
    except ValueError as exc:
        raise DockerRuntimeError(f"invalid docker wait result: {exit_text!r}") from exc

    log_text = _container_logs(distro, str(runtime["agent_container"]))
    trace_dir = Path(manifest["paths"]["trace"])
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "agent-container.log").write_text(log_text, encoding="utf-8")

    stopped = stop_docker_run(run_dir, wsl_distro=str(distro))
    completed_runtime = dict(stopped["runtime"])
    completed_runtime.update(
        {
            "agent_exit_code": exit_code,
            "completed_at": _utc_now(),
            "status": "completed" if exit_code == 0 else "failed",
        }
    )
    return update_manifest(
        run_dir,
        status="completed" if exit_code == 0 else "failed",
        runtime=completed_runtime,
    )


def read_docker_logs(
    run_dir: Path,
    *,
    tail: int = 200,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Read recent Agent logs without changing the run or its containers."""
    manifest = load_manifest(run_dir.resolve())
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("agent_container"):
        raise DockerRuntimeError("run has no Agent container")
    distro = wsl_distro or runtime.get(
        "wsl_distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", _DEFAULT_WSL_DISTRO)
    )
    result = _docker_raw(
        str(distro),
        "logs",
        "--timestamps",
        "--tail",
        str(tail),
        str(runtime["agent_container"]),
    )
    if result.returncode != 0:
        raise DockerRuntimeError(_command_error(result))
    return {
        "run_id": manifest["run"]["run_id"],
        "container": runtime["agent_container"],
        "logs": _combined_output(result),
    }


def stop_docker_run(
    run_dir: Path,
    *,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Stop and remove the containers/network owned by one run."""
    run_dir = run_dir.resolve()
    manifest = load_manifest(run_dir)
    runtime_value = manifest.get("runtime")
    runtime = dict(runtime_value) if isinstance(runtime_value, dict) else _runtime_metadata(
        str(manifest["run"]["run_id"])
    )
    distro = wsl_distro or runtime.get(
        "wsl_distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", _DEFAULT_WSL_DISTRO)
    )
    errors: list[str] = []

    for container_key in ("agent_container", "gateway_container"):
        container = runtime.get(container_key)
        if not container:
            continue
        result = _docker_raw(str(distro), "rm", "--force", str(container))
        if result.returncode not in (0, 1):
            errors.append(_command_error(result))

    network = runtime.get("network")
    if network:
        result = _docker_raw(str(distro), "network", "rm", str(network))
        if result.returncode not in (0, 1):
            errors.append(_command_error(result))

    runtime["status"] = "stopped"
    runtime["stopped_at"] = _utc_now()
    if errors:
        runtime["stop_errors"] = errors
    return update_manifest(run_dir, status="stopped", runtime=runtime)


def _agent_run_args(
    manifest: dict[str, Any],
    runtime: dict[str, str],
    *,
    gateway_key: str,
    image: str,
) -> list[str]:
    run = _run_metadata(manifest)
    paths = manifest["paths"]
    sandbox = _sandbox_metadata(run)
    args = [
        "run",
        "--detach",
        "--name",
        runtime["agent_container"],
        "--network",
        runtime["network"],
        *_container_security_args(sandbox, agent=True),
        "--add-host",
        "host.docker.internal:host-gateway",
        "--workdir",
        "/workspace/game-engine",
        "--env",
        f"EVAL_GATEWAY_API_KEY={gateway_key}",
        "--env",
        "EVAL_GATEWAY_URL=http://llm-gateway:8080/v1",
        "--env",
        f"EVAL_MODEL={run['model']}",
        "--env",
        f"EVAL_WIRE_API={run['protocol']}",
        "--env",
        f"EVAL_REASONING_EFFORT={run.get('reasoning_effort') or 'high'}",
        "--env",
        f"BLENDER_MCP_HOST={run.get('mcp_host', 'host.docker.internal')}",
        "--env",
        f"BLENDER_MCP_PORT={run.get('mcp_port', 9876)}",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "EVAL_MCP_SERVERS_FILE=/run-config/mcp-servers.json",
        "--env",
        "EVAL_DSH_MCP_SERVERS_FILE=/run-config/dsh-mcp-servers.json",
        "--env",
        f"EVAL_RUN_ID={run['run_id']}",
        "--env",
        f"EVAL_TASK_ID={run['task_id']}",
    ]

    _mount(args, Path(paths["project"]), "/workspace/game-engine")
    agent_config = paths.get("agent_config")
    if agent_config and Path(agent_config).is_dir():
        _mount(args, Path(agent_config), "/run-config", readonly=True)
    dsh_path = paths.get("dsh")
    if dsh_path and Path(dsh_path).is_dir():
        _mount(args, Path(dsh_path), "/workspace/ai-native-dsh")
    _mount(args, Path(paths["workspace"]), "/workspace/run-workspace")
    _mount(args, Path(paths["artifacts"]), "/workspace/artifacts")
    _mount(args, Path(paths["evidence"]), "/workspace/evidence")
    _mount(args, Path(paths["trace"]), "/workspace/trace")

    args.extend([image, str(run["task_prompt"])])
    return args


def _mount(args: list[str], source: Path, target: str, *, readonly: bool = False) -> None:
    mode = ",readonly" if readonly else ""
    args.extend(["--mount", f"type=bind,src={_linux_path(source)},dst={target}{mode}"])


def _runtime_metadata(run_id: str) -> dict[str, str]:
    safe = _safe_name(run_id)
    return {
        "network": f"ai-native-eval-{safe}",
        "gateway_container": f"ai-native-eval-{safe}-gateway",
        "agent_container": f"ai-native-eval-{safe}-agent",
    }


def _run_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("run")
    if not isinstance(value, dict):
        raise DockerRuntimeError("run manifest has no resolved run metadata")
    return value


def _sandbox_metadata(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("sandbox", {})
    return value if isinstance(value, dict) else {}


def _container_security_args(
    sandbox: dict[str, Any], *, agent: bool = False
) -> list[str]:
    args = ["--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
    pids_limit = sandbox.get("pids_limit")
    if pids_limit is not None:
        args.extend(["--pids-limit", str(int(pids_limit))])
    memory = sandbox.get("memory")
    if memory:
        args.extend(["--memory", str(memory)])
    if bool(sandbox.get("read_only_root", True)):
        args.extend(["--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m"])
        if agent:
            args.extend(
                [
                    "--tmpfs",
                    "/opt/codex-home:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=700",
                ]
            )
    return args


def _gateway_key(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return f"eval-{digest}"


def _safe_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )
    return normalized.strip("-")[:50] or "run"


def _docker(distro: str, *args: str) -> str:
    result = _docker_raw(distro, *args)
    if result.returncode != 0:
        raise DockerRuntimeError(_command_error(result))
    return _combined_output(result).strip()


def _docker_raw(distro: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["wsl.exe", "-d", distro, "--", "docker", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise DockerRuntimeError(f"could not invoke WSL Docker: {exc}") from exc


def _container_logs(distro: str, container: str) -> str:
    result = _docker_raw(distro, "logs", "--timestamps", container)
    if result.returncode != 0:
        raise DockerRuntimeError(_command_error(result))
    return _combined_output(result)


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "".join(part for part in (result.stdout, result.stderr) if part)


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    output = _combined_output(result).strip()
    return output or f"docker command failed with exit code {result.returncode}"


def _best_effort_stop(distro: str, runtime: dict[str, str]) -> None:
    for key in ("agent_container", "gateway_container"):
        _docker_raw(distro, "rm", "--force", runtime[key])
    _docker_raw(distro, "network", "rm", runtime["network"])


def _linux_path(path: Path) -> str:
    """Convert an absolute Windows path to a path visible inside WSL Docker."""
    resolved = path.resolve()
    drive = resolved.drive
    if drive:
        relative = resolved.relative_to(resolved.anchor).as_posix()
        return f"/mnt/{drive[0].lower()}/{relative}"
    return resolved.as_posix()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
