"""Tests for the WSL-backed Docker run command construction."""

from __future__ import annotations

import json
from pathlib import Path

import ai_native_evals.runs.docker_runtime as runtime
from ai_native_evals.runs.docker_runtime import _agent_run_args, _linux_path, start_docker_run
from ai_native_evals.runs.lifecycle import load_manifest


def _manifest(run_dir: Path) -> dict[str, object]:
    paths = {
        "project": str(run_dir / "project" / "game-engine"),
        "dsh": str(run_dir / "project" / "ai-native-dsh"),
        "workspace": str(run_dir / "workspace"),
        "artifacts": str(run_dir / "artifacts"),
        "evidence": str(run_dir / "evidence"),
        "trace": str(run_dir / "trace"),
    }
    for value in paths.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    return {
        "status": "prepared",
        "run": {
            "run_id": "blender-cube-abc123",
            "task_id": "blender-cube",
            "task_prompt": "Create a cube through Blender MCP.",
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
    assert _linux_path(Path("D:/work/AI-Native")) == "/mnt/d/work/AI-Native"


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
    assert args[-2:] == ["test-agent", "Create a cube through Blender MCP."]


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
