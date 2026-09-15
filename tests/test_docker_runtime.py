"""Tests for the WSL-backed Docker run command construction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ai_native_evals.runs.docker_runtime as runtime
from ai_native_evals.runs.docker_runtime import _agent_run_args, _linux_path, start_docker_run
from ai_native_evals.runs.lifecycle import load_manifest


def _manifest(run_dir: Path) -> dict[str, object]:
    paths = {
        "project": str(run_dir / "project" / "game-engine"),
        "dsh": str(run_dir / "project" / "ai-native-dsh"),
        "workspace": str(run_dir / "workspace"),
        "output": str(run_dir / "workspace" / "output"),
        "scratch": str(run_dir / "workspace" / "scratch"),
        "artifacts": str(run_dir / "workspace" / "artifacts"),
        "evidence": str(run_dir / "evidence"),
        "trace": str(run_dir / "trace"),
    }
    for value in paths.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    return {
        "status": "prepared",
        "run": {
            # A neutral fixture id: naming it after a real Task Bundle made the
            # test look like it depended on one.
            "run_id": "sample-task-abc123",
            "task_id": "sample-task",
            "task_prompt": "Create a cube through Blender MCP.",
            # A resolved run always carries its profile; the runtime refuses a
            # manifest without one, because every container decision reads it.
            "agent_profile": {
                "id": "codex",
                "adapter": "codex",
                "image": "test-agent",
                "protocol": "responses",
                "workdir": "/workspace/game-engine",
                "writable_paths": ["/opt/codex-home"],
                "capabilities": ["filesystem"],
                "options": {},
                "environment": {},
                "command": [],
                "entrypoint": None,
                "system_prompt": None,
                "trace_parser": "codex",
            },
            "agent_image": "test-agent",
            "model": "deepseek/deepseek-v4-flash",
            "protocol": "responses",
            "reasoning_effort": "high",
            "mcp_host": "host.docker.internal",
            "mcp_port": 9876,
        },
        "paths": paths,
    }


def test_linux_path_converts_windows_drive() -> None:
    """The drive translation is a Windows behaviour, so it is asserted on Windows.

    On Linux and macOS the same function must leave a path alone -- there is no
    WSL boundary to cross, and rewriting `/tmp/x` into `/mnt/...` would invent a
    path Docker cannot mount. Both directions are the same seam, so both are
    pinned here rather than only the one the development machine happened to
    exercise.
    """
    from ai_native_evals.runs import docker_cli

    if not docker_cli.is_windows():
        assert _linux_path(Path("/tmp/some/dir")) == "/tmp/some/dir"
        return
    # Any drive path works here; the conversion is what is under test.
    assert _linux_path(Path("D:/some/dir")) == "/mnt/d/some/dir"


def test_agent_command_mounts_snapshot_and_prompt(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    args = _agent_run_args(
        manifest,
        {
            "network": "eval-net",
            "agent_container": "eval-agent",
        },
        gateway_key="gateway-key",
        image="test-agent",
    )

    assert args[:2] == ["run", "--detach"]
    assert "--workdir" in args
    assert "/workspace/game-engine" in args
    assert "type=bind" in " ".join(args)
    assert "/workspace" in " ".join(args)
    # A profile with no command passes no prompt argument; the entrypoint reads
    # the mounted file. The prompt text must not appear anywhere in the argv.
    assert args[-1] == "test-agent"
    assert "Create a cube through Blender MCP." not in " ".join(args)
    assert "EVAL_TASK_PROMPT_FILE=/run-config/task-prompt.md" in args


def test_start_docker_run_records_runtime_without_calling_docker(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    env_file = tmp_path / ".env.local"
    env_file.write_text("AI_NATIVE_EVALS_LLM_API_KEY=test\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    def fake_docker(distro: str, *args: str) -> str:
        commands.append((distro, *args))
        return "container-id"

    monkeypatch.setattr(runtime, "_docker", fake_docker)
    result = start_docker_run(run_dir, tmp_path, gateway_env_file=env_file)

    assert result["status"] == "running"
    assert result["runtime"]["gateway_container"].endswith("-gateway")
    assert commands[0][1:3] == ("network", "create")
    assert any("ai-native-llm-gateway:local" in command for command in commands)
    assert any("test-agent" in command for command in commands)
    assert load_manifest(run_dir)["runtime"]["status"] == "running"


def test_agent_profile_controls_entrypoint_and_command_without_codex_branch(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["run"]["agent"] = "dsh"
    manifest["run"]["agent_profile"] = {
        "id": "dsh",
        "adapter": "dsh-acp",
        "image": "test-dsh",
        "workdir": "/workspace",
        "entrypoint": "node",
        "command": ["/run-config/dsh-acp-runner.mjs", "${TASK_PROMPT}"],
        "writable_paths": ["/tmp/dsh-home"],
    }
    args = _agent_run_args(
        manifest,
        {"network": "eval-net", "agent_container": "eval-agent"},
        gateway_key="gateway-key",
        image="test-dsh",
    )

    assert args[args.index("--entrypoint") + 1] == "node"
    assert "/tmp/dsh-home:rw" in " ".join(args)
    image_index = args.index("test-dsh")
    # `${TASK_PROMPT}` becomes the mounted file's path, not the prompt text: a
    # Markdown prompt passed as one argument loses its structure.
    assert args[image_index + 1 :] == [
        "/run-config/dsh-acp-runner.mjs",
        "/run-config/task-prompt.md",
    ]


def _running_manifest(tmp_path: Path) -> tuple[Path, dict]:
    manifest = _manifest(tmp_path)
    manifest["status"] = "running"
    manifest["run"]["sandbox"] = {"agent_timeout_seconds": 5}
    manifest["runtime"] = {
        "agent_container": "eval-agent",
        "gateway_container": "eval-gateway",
        "network": "eval-net",
        "status": "running",
        "wsl_distro": "Ubuntu-20.04",
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir, manifest


def test_agent_timeout_resolves_from_env_then_profile(monkeypatch) -> None:
    monkeypatch.delenv("AI_NATIVE_EVALS_AGENT_TIMEOUT_SECONDS", raising=False)
    assert runtime._agent_timeout_seconds({}) == runtime._DEFAULT_AGENT_TIMEOUT_SECONDS
    assert runtime._agent_timeout_seconds({"sandbox": {"agent_timeout_seconds": 42}}) == 42

    monkeypatch.setenv("AI_NATIVE_EVALS_AGENT_TIMEOUT_SECONDS", "7")
    assert runtime._agent_timeout_seconds({"sandbox": {"agent_timeout_seconds": 42}}) == 7

    monkeypatch.setenv("AI_NATIVE_EVALS_AGENT_TIMEOUT_SECONDS", "not-a-number")
    assert runtime._agent_timeout_seconds({}) == runtime._DEFAULT_AGENT_TIMEOUT_SECONDS


@pytest.mark.parametrize("call", ["_docker", "_docker_raw"])
def test_docker_helpers_never_run_unbounded(call: str, monkeypatch) -> None:
    """A Docker command that can hang forever can wedge the whole Console."""
    seen: list[int | None] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    getattr(runtime, call)("Ubuntu-20.04", "ps")

    assert seen and all(value is not None for value in seen), seen


def test_wait_docker_run_is_bounded_and_reclaims_containers(
    tmp_path: Path, monkeypatch
) -> None:
    """A hung Agent is stopped and reclaimed instead of waited on forever."""
    run_dir, _ = _running_manifest(tmp_path)
    calls: list[tuple[list[str], int | None]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(cmd), kwargs.get("timeout")))
        if "wait" in cmd:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 0)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    with pytest.raises(runtime.DockerTimeoutError, match="time limit"):
        runtime.wait_docker_run(run_dir)

    assert calls[0][1] == 5, "the Agent must be waited on with its own limit"
    removed = [args for args, _ in calls if "rm" in args]
    assert any("eval-agent" in args for args in removed)
    assert any("eval-gateway" in args for args in removed)
    assert all(timeout is not None for _, timeout in calls)

    after = load_manifest(run_dir)
    assert after["status"] == "stopped"
    assert after["runtime"]["status"] == "stopped"


# --------------------------------------------------------------------------
# Upstream identity is part of the reproducibility record.
# --------------------------------------------------------------------------


def _env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "env.local"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("UPSTREAM_BASE_URL=https://api.deepseek.com/v1" + chr(10), "https://api.deepseek.com"),
        ("AI_NATIVE_EVALS_LLM_BASE_URL=https://api.deepseek.com/v1" + chr(10), "https://api.deepseek.com"),
        ("UPSTREAM_BASE_URL=" + chr(34) + "https://api.deepseek.com/v1" + chr(34) + chr(10), "https://api.deepseek.com"),
        ("UPSTREAM_BASE_URL=" + chr(39) + "https://api.deepseek.com/v1" + chr(39) + chr(10), "https://api.deepseek.com"),
        ("export UPSTREAM_BASE_URL=https://api.deepseek.com/v1" + chr(10), "https://api.deepseek.com"),
        ("UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1" + chr(10), "http://127.0.0.1:8080"),
        ("UPSTREAM_BASE_URL=api.example.com/v1" + chr(10), "https://api.example.com"),
        ("SOMETHING_ELSE=1" + chr(10), None),
        ("# UPSTREAM_BASE_URL=https://api.deepseek.com/v1" + chr(10), None),
    ],
)
def test_upstream_origin_is_parsed_from_the_env_file(
    tmp_path: Path, content: str, expected: str | None
) -> None:
    assert runtime.gateway_upstream_origin(_env(tmp_path, content)) == expected


def test_upstream_origin_never_records_a_credential(tmp_path: Path) -> None:
    """Only the origin may reach the manifest; userinfo and query are dropped."""
    origin = runtime.gateway_upstream_origin(
        _env(
            tmp_path,
            "UPSTREAM_BASE_URL=https://user:sk-secret@api.example.com/v1?api_key=sk-secret"
            + chr(10),
        )
    )

    assert origin == "https://api.example.com"
    assert "sk-secret" not in (origin or "")
    assert "user" not in (origin or "")


def test_upstream_origin_tolerates_a_missing_env_file(tmp_path: Path) -> None:
    assert runtime.gateway_upstream_origin(tmp_path / "absent") is None