"""Run evaluator Agents in isolated Docker sandboxes.

The subject Agent and all Agent-backed evaluators share this runner. They never
share a writable container: a role receives its own ephemeral network, gateway,
container, trace directory, and permissions while reading the parent run's
persisted workspace/evidence through explicit mounts.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .docker_runtime import (
    DockerRuntimeError,
    _container_security_args,
    _docker,
    _docker_raw,
    _gateway_key,
    _linux_path,
    _mount,
    _resolve_wsl_host_ip,
    _safe_name,
)
from .lifecycle import load_manifest


@dataclass(frozen=True, slots=True)
class EvaluatorAgentResult:
    """Persisted outcome of one evaluator Agent sandbox run."""

    run_id: str
    role: str
    status: str
    exit_code: int | None
    started_at: str
    finished_at: str
    trace_dir: Path
    log_path: Path
    last_message_path: Path
    output_path: Path | None = None
    failure_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe metadata."""
        return {
            "run_id": self.run_id,
            "role": self.role,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "trace_dir": str(self.trace_dir),
            "log_path": str(self.log_path),
            "last_message_path": str(self.last_message_path),
            "output_path": str(self.output_path) if self.output_path else None,
            "failure_message": self.failure_message,
        }


def run_evaluator_agent(
    run_dir: str | Path,
    *,
    role: str,
    prompt: str,
    mounts: list[tuple[str | Path, str, bool]],
    output_filename: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    image: str | None = None,
    model: str | None = None,
    protocol: str | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: int = 900,
    wsl_distro: str | None = None,
    gateway_image: str | None = None,
    gateway_env_file: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> EvaluatorAgentResult:
    """Run one read-only or role-specific Agent evaluator in Docker.

    ``mounts`` contains ``(host_source, container_target, readonly)`` entries.
    The role trace directory is always mounted read-write at
    ``/workspace/trace`` so the bundled Codex entrypoint can save its final
    message and the evaluator can write a structured result there.
    """
    run_dir = Path(run_dir).resolve()
    manifest = load_manifest(run_dir)
    run = manifest.get("run")
    paths = manifest.get("paths")
    if not isinstance(run, dict) or not isinstance(paths, dict):
        raise DockerRuntimeError("run manifest is missing run or paths metadata")

    run_id = str(run.get("run_id", run_dir.name))
    safe_role = _safe_name(role)
    # Keep the nonce before the long role name: _safe_name truncates from the
    # right, so putting it at the end would lose uniqueness and recreate the
    # Docker name-conflict bug on retries. The same nonce also versions the
    # durable evaluator trace so repeated evaluations never overwrite history.
    nonce = uuid4().hex[:8]
    resource_name = _safe_name(f"{run_id}-{nonce}-{safe_role}")
    # Docker names have a length limit. Reserve room for container suffixes;
    # otherwise gateway and agent names can be truncated to the same string.
    network = _safe_name(f"ai-native-eval-{resource_name}-eval")
    gateway_container = f"{network}-gateway"
    agent_container = f"{network}-agent"
    distro = wsl_distro or str(
        (run.get("sandbox") or {}).get(
            "distro", os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO", "Ubuntu-20.04")
        )
    )
    sandbox = run.get("sandbox") if isinstance(run.get("sandbox"), dict) else {}
    selected_gateway_image = gateway_image or str(
        sandbox.get(
            "gateway_image",
            os.environ.get("AI_NATIVE_EVALS_GATEWAY_IMAGE", "ai-native-llm-gateway:local"),
        )
    )
    if gateway_env_file is None:
        runtime = manifest.get("runtime")
        if isinstance(runtime, dict) and runtime.get("gateway_env_file"):
            gateway_env_file = runtime["gateway_env_file"]
        else:
            root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[3]
            gateway_env_file = root / "config" / ".env.local"
    env_file = Path(str(gateway_env_file)).resolve()
    if not env_file.is_file():
        raise DockerRuntimeError(f"gateway env file does not exist: {env_file}")

    trace_root = Path(str(paths.get("trace", run_dir / "trace"))).resolve()
    role_trace = trace_root / "evaluators" / safe_role / nonce
    role_trace.mkdir(parents=True, exist_ok=True)
    log_path = role_trace / "agent-container.log"
    last_message_path = role_trace / "codex-last-message.txt"
    output_path = role_trace / output_filename if output_filename else None

    config_root = Path(str(paths.get("agent_config", run_dir / "agent-config"))).resolve()
    evaluator_config_root = config_root / "evaluators"
    evaluator_config_root.mkdir(parents=True, exist_ok=True)
    mcp_path = evaluator_config_root / f"{safe_role}-mcp.json"
    mcp_path.write_text(
        json.dumps(mcp_servers or {}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    resolved_mounts: list[tuple[Path, str, bool]] = []
    for source, target, readonly in mounts:
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise DockerRuntimeError(f"evaluator mount source does not exist: {source_path}")
        resolved_mounts.append((source_path, target, readonly))

    started = _utc_now()
    exit_code: int | None = None
    status = "failed"
    failure_message: str | None = None
    gateway_key = _gateway_key(f"{run_id}-{safe_role}")
    network_created = False
    try:
        _docker(distro, "network", "create", network)
        network_created = True
        _docker(
            distro,
            "run",
            "--detach",
            "--name",
            gateway_container,
            "--network",
            network,
            "--network-alias",
            "llm-gateway",
            *_container_security_args(sandbox),
            "--env-file",
            _linux_path(env_file),
            "--env",
            f"GATEWAY_API_KEY={gateway_key}",
            "--env",
            f"DEFAULT_MODEL={model or run.get('model', '')}",
            selected_gateway_image,
        )

        agent_args = [
            "run",
            "--detach",
            "--name",
            agent_container,
            "--network",
            network,
            *_container_security_args(sandbox, agent=True),
            "--add-host",
            f"host.docker.internal:{_resolve_wsl_host_ip(distro)}",
            "--workdir",
            "/workspace/game-engine",
            "--env",
            f"EVAL_GATEWAY_API_KEY={gateway_key}",
            "--env",
            "EVAL_GATEWAY_URL=http://llm-gateway:8080/v1",
            "--env",
            f"EVAL_MODEL={model or run.get('model', '')}",
            "--env",
            f"EVAL_WIRE_API={protocol or run.get('protocol', 'responses')}",
            "--env",
            f"EVAL_REASONING_EFFORT={reasoning_effort or run.get('reasoning_effort') or 'high'}",
            "--env",
            "EVAL_OUTER_SANDBOX=docker",
            "--env",
            "HOME=/tmp/home",
            "--env",
            "EVAL_MCP_SERVERS_FILE=/run-config/mcp-servers.json",
            "--env",
            f"EVAL_RUN_ID={run_id}",
            "--env",
            f"EVAL_TASK_ID={run.get('task_id', '')}",
            "--mount",
            f"type=bind,src={_linux_path(mcp_path)},dst=/run-config/mcp-servers.json,readonly",
            "--mount",
            f"type=bind,src={_linux_path(role_trace)},dst=/workspace/trace",
        ]
        for source, target, readonly in resolved_mounts:
            _mount(agent_args, source, target, readonly=readonly)

        agent_args.extend([str(image or run.get("agent_image", "")), prompt])
        _docker(distro, *agent_args)

        wait_result = _docker_wait(distro, agent_container, timeout_seconds)
        if wait_result.returncode != 0:
            raise DockerRuntimeError(_command_error(wait_result))
        try:
            exit_code = int(_combined_output(wait_result).strip())
        except ValueError as exc:
            raise DockerRuntimeError(
                f"invalid evaluator docker wait result: {_combined_output(wait_result)!r}"
            ) from exc
        logs_result = _docker_raw(distro, "logs", "--timestamps", agent_container)
        if logs_result.returncode != 0:
            raise DockerRuntimeError(_command_error(logs_result))
        log_path.write_text(_combined_output(logs_result), encoding="utf-8")
        status = "completed" if exit_code == 0 else "failed"
        if status == "failed":
            failure_message = _tail(_combined_output(logs_result)) or (
                f"evaluator exited with {exit_code}"
            )
    except (DockerRuntimeError, OSError) as exc:
        failure_message = str(exc)
        status = "failed"
    finally:
        # Evaluator resources are ephemeral; all durable state is in role_trace.
        for container in (agent_container, gateway_container):
            _docker_raw(distro, "rm", "--force", container)
        if network_created:
            _docker_raw(distro, "network", "rm", network)

    finished = _utc_now()
    return EvaluatorAgentResult(
        run_id=run_id,
        role=role,
        status=status,
        exit_code=exit_code,
        started_at=started,
        finished_at=finished,
        trace_dir=role_trace,
        log_path=log_path,
        last_message_path=last_message_path,
        output_path=output_path,
        failure_message=failure_message,
    )


def default_readonly_mounts(manifest: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Return standard candidate workspace mounts for evaluator Agents."""
    paths = manifest.get("paths")
    if not isinstance(paths, dict):
        raise DockerRuntimeError("manifest has no paths")
    workspace = Path(str(paths["workspace"]))
    mounts: list[tuple[str, str, bool]] = []
    # Mount each child explicitly so /workspace/trace can remain role-writable.
    for key, target in (
        ("project", "/workspace/game-engine"),
        ("dsh", "/workspace/ai-native-dsh"),
        ("output", "/workspace/output"),
        ("scratch", "/workspace/scratch"),
        ("evidence", "/workspace/evidence"),
    ):
        raw = paths.get(key)
        source = Path(str(raw)) if raw else workspace / target.rsplit("/", 1)[-1]
        if source.is_dir():
            mounts.append((source, target, True))
    return mounts


def _docker_wait(
    distro: str, container: str, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    """Wait for a role container without allowing a hung Judge forever."""
    try:
        return subprocess.run(
            ["wsl.exe", "-d", distro, "--", "docker", "wait", container],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerRuntimeError(
            f"evaluator Agent exceeded timeout of {timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise DockerRuntimeError(f"could not invoke WSL Docker wait: {exc}") from exc


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "".join(part for part in (result.stdout, result.stderr) if part)


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    output = _combined_output(result).strip()
    return output or f"docker command failed with exit code {result.returncode}"


def _tail(value: str, limit: int = 1000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
