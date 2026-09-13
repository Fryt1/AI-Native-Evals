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
from urllib.parse import urlsplit

from .lifecycle import load_manifest, update_manifest


class DockerRuntimeError(RuntimeError):
    """Raised when the WSL-backed Docker runtime cannot manage a run."""


class DockerTimeoutError(DockerRuntimeError):
    """Raised when a Docker command exceeds its allotted time."""


_DEFAULT_WSL_DISTRO = "Ubuntu-20.04"
_DEFAULT_GATEWAY_IMAGE = "ai-native-llm-gateway:local"
# No Docker call may block forever. `docker wait` overrides this with the run's
# own Agent time limit, which is far longer by design.
_DEFAULT_DOCKER_TIMEOUT_SECONDS = 300
_DEFAULT_AGENT_TIMEOUT_SECONDS = 1800


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
    # Precedence: an explicit argument, then the provider this run selected,
    # then an operator-level default. A run's chosen provider must not be
    # silently overridden by an ambient variable.
    declared_env_file = run.get("provider_env_file")
    env_file = (
        gateway_env_file
        or (Path(str(declared_env_file)) if declared_env_file else None)
        or Path(
            os.environ.get(
                "AI_NATIVE_EVALS_GATEWAY_ENV_FILE", str(repo_root / "config" / ".env.local")
            )
        )
    ).resolve()

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
            # Which upstream actually served this run. The manifest is the
            # reproducibility record; a gitignored .env.local is not.
            "upstream_origin": gateway_upstream_origin(env_file),
            "started_at": _utc_now(),
            "status": "running",
        }
    )
    return update_manifest(run_dir, status="running", runtime=runtime)


def wait_docker_run(
    run_dir: Path,
    *,
    wsl_distro: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Wait for the Agent, persist its logs, and release Docker resources.

    The wait is bounded. An Agent that hangs must not hold its container, its run
    record, or the caller forever; on timeout the containers and network are
    reclaimed and the run is reported as failed rather than left running.
    """
    run_dir = run_dir.resolve()
    manifest = load_manifest(run_dir)
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("agent_container"):
        raise DockerRuntimeError("run has no Agent container")
    distro = wsl_distro or runtime.get(
        "wsl_distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", _DEFAULT_WSL_DISTRO)
    )
    run = manifest.get("run")
    limit = timeout_seconds or _agent_timeout_seconds(run if isinstance(run, dict) else {})

    try:
        exit_text = _docker(
            distro, "wait", str(runtime["agent_container"]), timeout=limit
        )
    except DockerTimeoutError as exc:
        try:
            stop_docker_run(run_dir, wsl_distro=str(distro))
        except (DockerRuntimeError, OSError, ValueError):
            # Reclaiming what we can beats masking the original timeout.
            pass
        raise DockerTimeoutError(
            f"Agent exceeded its {limit}s time limit and was stopped"
        ) from exc
    try:
        exit_code = int(exit_text.strip())
    except ValueError as exc:
        raise DockerRuntimeError(f"invalid docker wait result: {exit_text!r}") from exc

    log_text = _container_logs(distro, str(runtime["agent_container"]))
    trace_dir = Path(manifest["paths"]["trace"])
    trace_dir.mkdir(parents=True, exist_ok=True)
    log_path = trace_dir / "agent-container.log"
    log_path.write_text(log_text, encoding="utf-8")
    try:
        from ..adapters.events import normalize_log_file

        profile = manifest.get("run", {}).get("agent_profile", {})
        adapter = profile.get("adapter") if isinstance(profile, dict) else None
        normalize_log_file(
            log_path,
            trace_dir / "normalized-events.jsonl",
            adapter=str(adapter or manifest.get("run", {}).get("agent", "codex")),
            agent_id=str(manifest.get("run", {}).get("agent", "agent")),
        )
    except Exception:
        # Normalization is observability; it must never mask the Agent exit code.
        pass
    # Digest generation is best-effort telemetry; it must never mask the Agent
    # exit code or prevent Docker resource cleanup.
    try:
        from ..adapters.run_digest import write_digest_files

        write_digest_files(run_dir)
    except Exception:
        pass

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
    """Build a provider-neutral Agent container command from the run profile."""
    run = _run_metadata(manifest)
    paths = manifest["paths"]
    sandbox = _sandbox_metadata(run)
    profile = run.get("agent_profile") if isinstance(run.get("agent_profile"), dict) else {}
    legacy_profile = not bool(profile)
    workdir = str(
        profile.get("workdir")
        or ("/workspace/game-engine" if legacy_profile else "/workspace")
    )
    adapter = str(profile.get("adapter") or run.get("agent", "codex"))
    profile_id = str(profile.get("id") or run.get("agent", adapter))
    writable_paths = profile.get("writable_paths", [])
    if not isinstance(writable_paths, list):
        writable_paths = []
    if not writable_paths and legacy_profile:
        writable_paths = ["/opt/codex-home"]
    args = [
        "run",
        "--detach",
        "--name",
        runtime["agent_container"],
        "--network",
        runtime["network"],
        *_container_security_args(sandbox, agent=True, writable_paths=writable_paths),
        "--add-host",
        f"host.docker.internal:{_resolve_wsl_host_ip(str(sandbox.get('distro', 'Ubuntu-20.04')))}",
        "--workdir",
        workdir,
        "--env",
        f"EVAL_GATEWAY_API_KEY={gateway_key}",
        "--env",
        "EVAL_GATEWAY_URL=http://llm-gateway:8080/v1",
        "--env",
        f"EVAL_MODEL={run.get('model', '')}",
        "--env",
        f"EVAL_MODEL_PROVIDER={run.get('model_provider') or 'eval'}",
        "--env",
        f"EVAL_WIRE_API={run.get('protocol', 'responses')}",
        "--env",
        f"EVAL_REASONING_EFFORT={run.get('reasoning_effort') or 'high'}",
        "--env",
        f"EVAL_AGENT_ID={run.get('agent', profile_id)}",
        "--env",
        f"EVAL_AGENT_ADAPTER={adapter}",
        "--env",
        f"EVAL_AGENT_PROFILE={profile_id}",
        "--env",
        f"EVAL_WORKDIR={workdir}",
        "--env",
        "EVAL_TRACE_DIR=/workspace/trace",
        "--env",
        "EVAL_LAST_MESSAGE_PATH=/workspace/trace/agent-last-message.txt",
        "--env",
        "EVAL_OUTER_SANDBOX=docker",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "EVAL_MCP_SERVERS_FILE=/run-config/mcp-servers.json",
        "--env",
        "EVAL_DSH_MCP_SERVERS_FILE=/run-config/dsh-mcp-servers.json",
        "--env",
        "EVAL_AGENT_PROFILE_FILE=/run-config/agent-profile.json",
        "--env",
        "EVAL_SYSTEM_PROMPT_FILE=/run-config/agent-system-prompt.txt",
        "--env",
        f"EVAL_RUN_ID={run['run_id']}",
        "--env",
        f"EVAL_TASK_ID={run['task_id']}",
    ]
    for key, value in (profile.get("environment") or {}).items():
        args.extend(["--env", f"{key}={_render_runtime_value(str(value), run, workdir)}"])

    _mount(args, Path(paths["workspace"]), "/workspace")
    agent_config = paths.get("agent_config")
    if agent_config and Path(agent_config).is_dir():
        _mount(args, Path(agent_config), "/run-config", readonly=True)

    command = profile.get("command", [])
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list):
        command = []
    rendered_command = [
        _render_runtime_value(str(value), run, workdir)
        for value in command
    ]
    if not rendered_command:
        rendered_command = [str(run["task_prompt"])]
    elif "${TASK_PROMPT}" not in command:
        rendered_command.append(str(run["task_prompt"]))
    entrypoint = profile.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint:
        args.extend(["--entrypoint", entrypoint])
    args.extend([image, *rendered_command])
    return args


def _render_runtime_value(value: str, run: dict[str, Any], workdir: str) -> str:
    """Resolve only non-secret run placeholders in a profile command/env."""
    return (
        value.replace("${TASK_PROMPT}", str(run.get("task_prompt", "")))
        .replace("${TASK_ID}", str(run.get("task_id", "")))
        .replace("${RUN_ID}", str(run.get("run_id", "")))
        .replace("${WORKDIR}", workdir)
        .replace("${MODEL}", str(run.get("model", "")))
        .replace("${MODEL_PROVIDER}", str(run.get("model_provider", "")))
        .replace("${REASONING_EFFORT}", str(run.get("reasoning_effort", "")))
        .replace("${MCP_CONFIG}", "/run-config/mcp-servers.json")
        .replace("${DSH_MCP_CONFIG}", "/run-config/dsh-mcp-servers.json")
    )


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


def _agent_timeout_seconds(run: dict[str, Any]) -> int:
    """Resolve the subject Agent time limit from the environment, then the profile."""
    configured = os.environ.get("AI_NATIVE_EVALS_AGENT_TIMEOUT_SECONDS")
    if configured:
        try:
            value = int(configured)
        except ValueError:
            value = 0
        if value > 0:
            return value
    sandbox = _sandbox_metadata(run)
    value = sandbox.get("agent_timeout_seconds")
    if isinstance(value, bool):
        value = None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return _DEFAULT_AGENT_TIMEOUT_SECONDS


def _sandbox_metadata(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("sandbox", {})
    return value if isinstance(value, dict) else {}


def _container_security_args(
    sandbox: dict[str, Any], *, agent: bool = False, writable_paths: list[str] | None = None
) -> list[str]:
    """Return common container isolation flags without naming an Agent."""
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
            for path in writable_paths or []:
                safe_path = str(path).strip()
                if safe_path:
                    args.extend(
                        [
                            "--tmpfs",
                            f"{safe_path}:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=700",
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


def _env_file_value(env_file: Path, *names: str) -> str | None:
    """Read the first matching KEY=VALUE from a dotenv-style file."""
    try:
        raw = env_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    wanted = set(names)
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, sep, value = stripped.partition("=")
        if not sep or key.strip() not in wanted:
            continue
        value = value.strip().strip("\"'")
        if value:
            return value
    return None


def gateway_upstream_origin(env_file: Path) -> str | None:
    """Return scheme://host of the configured LLM upstream.

    A run must be able to answer "did the upstream change between these two
    runs?" without recording a credential, so only the origin is kept: never
    the userinfo, path, query, or the API key.
    """
    raw = _env_file_value(env_file, "UPSTREAM_BASE_URL", "AI_NATIVE_EVALS_LLM_BASE_URL")
    if not raw:
        return None
    try:
        parts = urlsplit(raw if "//" in raw else "//" + raw)
    except ValueError:
        return None
    host = parts.hostname
    if not host:
        return None
    origin = (parts.scheme or "https") + "://" + host
    if parts.port:
        origin = origin + ":" + str(parts.port)
    return origin


def _resolve_wsl_host_ip(distro: str) -> str:
    """Resolve the Windows host address reachable from a WSL Docker container."""
    result = subprocess.run(
        [
            "wsl.exe",
            "-d",
            distro,
            "--",
            "sh",
            "-lc",
            "ip route | sed -n 's/^default via \\([^ ]*\\).*/\\1/p'",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    candidate = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if candidate:
        return candidate
    # Older Docker Desktop configurations understand host-gateway directly.
    return "host-gateway"


def _docker(
    distro: str, *args: str, timeout: int | None = _DEFAULT_DOCKER_TIMEOUT_SECONDS
) -> str:
    result = _docker_raw(distro, *args, timeout=timeout)
    if result.returncode != 0:
        raise DockerRuntimeError(_command_error(result))
    return _combined_output(result).strip()


def _docker_raw(
    distro: str, *args: str, timeout: int | None = _DEFAULT_DOCKER_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["wsl.exe", "-d", distro, "--", "docker", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerTimeoutError(
            f"docker {' '.join(args[:2])} exceeded {timeout}s"
        ) from exc
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
