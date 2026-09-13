"""Run evaluator Agents in isolated Docker sandboxes.

The subject Agent and all Agent-backed evaluators share this generic runner. A
profile selects the Agent image/adapter; this module only owns Docker, mounts,
network isolation, and durable role evidence.
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

from ..adapters.events import normalize_log_file
from ..mcp import project_dsh_mcp_servers
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
    normalized_events_path: Path | None = None
    adapter: str | None = None

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
            "normalized_events_path": (
                str(self.normalized_events_path) if self.normalized_events_path else None
            ),
            "adapter": self.adapter,
        }


def run_evaluator_agent(
    run_dir: str | Path,
    *,
    role: str,
    prompt: str,
    mounts: list[tuple[str | Path, str, bool]],
    output_filename: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    agent_profile: dict[str, Any] | None = None,
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
    The role trace directory is always mounted read-write at ``/workspace/trace``.
    Agent-specific launch details come only from ``agent_profile``.
    """
    run_dir = Path(run_dir).resolve()
    manifest = load_manifest(run_dir)
    run = manifest.get("run")
    paths = manifest.get("paths")
    if not isinstance(run, dict) or not isinstance(paths, dict):
        raise DockerRuntimeError("run manifest is missing run or paths metadata")

    profile = agent_profile or (
        run.get("agent_profile") if isinstance(run.get("agent_profile"), dict) else {}
    )
    legacy_profile = not bool(profile)
    adapter = str(profile.get("adapter") or run.get("agent", "codex"))
    profile_id = str(profile.get("id") or run.get("agent", adapter))
    # The log parser is its own fact, resolved by the profile. It used to be the
    # adapter value, which made an Agent's identity decide how its log is read.
    trace_parser = str(profile.get("trace_parser") or adapter)
    workdir = str(
        profile.get("workdir")
        or ("/workspace/game-engine" if legacy_profile else "/workspace")
    )
    writable_paths = profile.get("writable_paths", [])
    if not isinstance(writable_paths, list):
        writable_paths = []
    if not writable_paths and legacy_profile:
        writable_paths = ["/opt/codex-home"]

    run_id = str(run.get("run_id", run_dir.name))
    safe_role = _safe_name(role)
    nonce = uuid4().hex[:8]
    resource_name = _safe_name(f"{run_id}-{nonce}-{safe_role}")
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
        # Follow the provider the subject run used, so an evaluator grades
        # through the same upstream rather than whichever default is ambient.
        runtime = manifest.get("runtime")
        declared_env_file = run.get("provider_env_file")
        if isinstance(runtime, dict) and runtime.get("gateway_env_file"):
            gateway_env_file = runtime["gateway_env_file"]
        elif declared_env_file:
            gateway_env_file = declared_env_file
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
    last_message_path = role_trace / "agent-last-message.txt"
    output_path = role_trace / output_filename if output_filename else None
    normalized_events_path = role_trace / "normalized-events.jsonl"

    config_root = Path(str(paths.get("agent_config", run_dir / "agent-config"))).resolve()
    evaluator_config_root = config_root / "evaluators"
    evaluator_config_root.mkdir(parents=True, exist_ok=True)
    mcp_value = mcp_servers if mcp_servers is not None else run.get("mcp_servers", {})
    if not isinstance(mcp_value, dict):
        mcp_value = {}
    mcp_path = evaluator_config_root / f"{safe_role}-mcp.json"
    mcp_path.write_text(
        json.dumps(mcp_value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    dsh_mcp_path = evaluator_config_root / f"{safe_role}-dsh-mcp.json"
    dsh_mcp_path.write_text(
        json.dumps(project_dsh_mcp_servers(mcp_value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_path = evaluator_config_root / f"{safe_role}-agent-profile.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    system_prompt_path = evaluator_config_root / f"{safe_role}-system-prompt.txt"
    if profile.get("system_prompt"):
        system_prompt_path.write_text(str(profile["system_prompt"]), encoding="utf-8")
    dsh_runner_path = evaluator_config_root / "dsh-acp-runner.mjs"
    root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[3]
    dsh_runner_source = root / "docker" / "dsh-agent" / "acp-runner.mjs"
    if dsh_runner_source.is_file():
        dsh_runner_path.write_text(dsh_runner_source.read_text(encoding="utf-8"), encoding="utf-8")

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
            f"GATEWAY_API_KEY={_gateway_key(f'{run_id}-{safe_role}')}",
            "--env",
            f"DEFAULT_MODEL={model or run.get('model', '')}",
            selected_gateway_image,
        )

        gateway_key = _gateway_key(f"{run_id}-{safe_role}")
        agent_args = [
            "run",
            "--detach",
            "--name",
            agent_container,
            "--network",
            network,
            *_container_security_args(sandbox, agent=True, writable_paths=writable_paths),
            "--add-host",
            f"host.docker.internal:{_resolve_wsl_host_ip(distro)}",
            "--workdir",
            workdir,
            "--env",
            f"EVAL_GATEWAY_API_KEY={gateway_key}",
            "--env",
            "EVAL_GATEWAY_URL=http://llm-gateway:8080/v1",
            "--env",
            f"EVAL_MODEL={model or run.get('model', '')}",
            "--env",
            f"EVAL_MODEL_PROVIDER={run.get('model_provider') or 'eval'}",
            "--env",
            f"EVAL_WIRE_API={protocol or run.get('protocol', 'responses')}",
            "--env",
            f"EVAL_REASONING_EFFORT={reasoning_effort or run.get('reasoning_effort') or 'high'}",
            "--env",
            f"EVAL_AGENT_ID={profile_id}",
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
            f"EVAL_RUN_ID={run_id}",
            "--env",
            f"EVAL_TASK_ID={run.get('task_id', '')}",
        ]
        for key, value in (profile.get("environment") or {}).items():
            agent_args.extend(
                [
                    "--env",
                    f"{key}={_render_profile_value(str(value), prompt, run, workdir)}",
                ]
            )
        agent_args.extend(
            [
                "--mount",
                f"type=bind,src={_linux_path(mcp_path)},dst=/run-config/mcp-servers.json,readonly",
                "--mount",
                f"type=bind,src={_linux_path(dsh_mcp_path)},dst=/run-config/dsh-mcp-servers.json,readonly",
                "--mount",
                f"type=bind,src={_linux_path(profile_path)},dst=/run-config/agent-profile.json,readonly",
            ]
        )
        if dsh_runner_path.is_file():
            agent_args.extend(
                [
                    "--mount",
                    f"type=bind,src={_linux_path(dsh_runner_path)},dst=/run-config/dsh-acp-runner.mjs,readonly",
                ]
            )
        if system_prompt_path.is_file():
            agent_args.extend(
                [
                    "--mount",
                    f"type=bind,src={_linux_path(system_prompt_path)},dst=/run-config/agent-system-prompt.txt,readonly",
                ]
            )
        agent_args.extend(
            [
                "--mount",
                f"type=bind,src={_linux_path(role_trace)},dst=/workspace/trace",
            ]
        )
        for source, target, readonly in resolved_mounts:
            _mount(agent_args, source, target, readonly=readonly)

        command = profile.get("command", [])
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list):
            command = []
        rendered = [
            _render_profile_value(str(value), prompt, run, workdir)
            for value in command
        ]
        if not rendered:
            rendered = [prompt]
        elif "${TASK_PROMPT}" not in command:
            rendered.append(prompt)
        entrypoint = profile.get("entrypoint")
        if isinstance(entrypoint, str) and entrypoint:
            agent_args.extend(["--entrypoint", entrypoint])
        agent_args.extend(
            [str(image or profile.get("image") or run.get("agent_image", "")), *rendered]
        )
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
        log_text = _combined_output(logs_result)
        log_path.write_text(log_text, encoding="utf-8")
        normalize_log_file(
            log_path, normalized_events_path, adapter=trace_parser, agent_id=profile_id
        )
        status = "completed" if exit_code == 0 else "failed"
        if status == "failed":
            failure_message = _tail(log_text) or f"evaluator exited with {exit_code}"
    except (DockerRuntimeError, OSError) as exc:
        failure_message = str(exc)
        status = "failed"
    finally:
        for container in (agent_container, gateway_container):
            _docker_raw(distro, "rm", "--force", container)
        if network_created:
            _docker_raw(distro, "network", "rm", network)

    if last_message_path.is_file():
        (role_trace / "codex-last-message.txt").write_text(
            last_message_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )
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
        normalized_events_path=normalized_events_path,
        adapter=adapter,
    )


def default_readonly_mounts(manifest: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Return standard candidate workspace mounts for evaluator Agents."""
    paths = manifest.get("paths")
    if not isinstance(paths, dict):
        raise DockerRuntimeError("manifest has no paths")
    workspace = Path(str(paths["workspace"]))
    mounts: list[tuple[str, str, bool]] = []
    mounted_targets: set[str] = set()
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
            mounted_targets.add(str(source.resolve()))

    resources = paths.get("resources")
    if isinstance(resources, dict):
        for raw in resources.values():
            source = Path(str(raw)).resolve()
            if not source.is_dir() or str(source) in mounted_targets:
                continue
            try:
                relative = source.relative_to(workspace.resolve()).as_posix()
            except ValueError:
                continue
            mounts.append((source, f"/workspace/{relative}", True))
    return mounts


def _render_profile_value(value: str, prompt: str, run: dict[str, Any], workdir: str) -> str:
    return (
        value.replace("${TASK_PROMPT}", prompt)
        .replace("${TASK_ID}", str(run.get("task_id", "")))
        .replace("${RUN_ID}", str(run.get("run_id", "")))
        .replace("${WORKDIR}", workdir)
        .replace("${MODEL}", str(run.get("model", "")))
        .replace("${MODEL_PROVIDER}", str(run.get("model_provider", "")))
        .replace("${REASONING_EFFORT}", str(run.get("reasoning_effort", "")))
        .replace("${MCP_CONFIG}", "/run-config/mcp-servers.json")
        .replace("${DSH_MCP_CONFIG}", "/run-config/dsh-mcp-servers.json")
    )


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
