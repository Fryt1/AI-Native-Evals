"""Tests for the declarative per-run MCP profile contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_native_evals.mcp import project_dsh_mcp_servers
from ai_native_evals.runs.resolver import resolve_run


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, mcp_profile: str, servers_yaml: str) -> Path:
    """A minimal repository, with profiles and a Task in their real locations.

    Built as files rather than as inline config: the resolver reads profiles and
    Tasks from disk only.
    """
    _write(
        tmp_path / "config" / "eval.yaml",
        f"""
version: 1
task_roots:
- tasks
profile_roots:
  agents: profiles/agents
  models: profiles/models
  mcp: profiles/mcp
  sandboxes: profiles/sandboxes
paths:
  runs_root: EvalRuns
defaults:
  agent: codex
  model_profile: test-model
  mcp_profile: {mcp_profile}
""",
    )
    _write(
        tmp_path / "profiles" / "agents" / "codex.yaml",
        "id: codex\nadapter: codex\nimage: test-codex\n",
    )
    _write(
        tmp_path / "profiles" / "models" / "test-model.yaml",
        "id: test-model\nmodel: test/model\nprotocol: responses\n",
    )
    _write(
        tmp_path / "profiles" / "mcp" / f"{mcp_profile}.yaml",
        f"id: {mcp_profile}\n{servers_yaml}",
    )
    _write(
        tmp_path / "tasks" / "test" / "task.yaml",
        'id: test\nversion: 1\nprompt_file: prompt.md\nresources: []\n',
    )
    _write(tmp_path / "tasks" / "test" / "prompt.md", "Do the thing.")
    return tmp_path


def test_resolve_run_substitutes_mcp_profile_variables(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        "ue5",
        """
host: host.docker.internal
port: 8000
servers:
  unreal-mcp:
    transport: streamable-http
    url: http://${MCP_HOST}:${MCP_PORT}/mcp
  extra:
    transport: stdio
    command: /opt/extra/server
    env:
      TARGET: ${MCP_HOST}
""",
    )

    spec = resolve_run(root, "test")

    assert spec.mcp_servers["unreal-mcp"]["url"] == "http://host.docker.internal:8000/mcp"
    assert spec.mcp_servers["extra"]["env"]["TARGET"] == "host.docker.internal"


def test_prepare_run_persists_resolved_mcp_servers(tmp_path: Path) -> None:
    root = _repo(tmp_path, "none", "servers: {}\n")

    spec = resolve_run(root, "test")
    from ai_native_evals.runs.lifecycle import load_manifest, prepare_run

    run_dir = prepare_run(root, spec)
    manifest = load_manifest(run_dir)
    mcp_path = Path(manifest["paths"]["mcp_servers"])
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == {}


def _renderer_env(**overrides: str) -> dict[str, str]:
    """Minimal env for the Node renderer: OS basics plus the values under test."""
    import os

    env = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(overrides)
    return env


def test_codex_config_renderer_writes_stdio_and_http_servers(tmp_path: Path) -> None:
    output = tmp_path / "config.toml"
    servers = tmp_path / "servers.json"
    servers.write_text(
        json.dumps(
            {
                "blender": {
                    "transport": "stdio",
                    "command": "/opt/blender-mcp/bin/blender-mcp",
                    "env": {"BLENDER_MCP_PORT": "9876"},
                },
                "unreal-mcp": {
                    "transport": "streamable-http",
                    "url": "http://host.docker.internal:8000/mcp",
                },
            }
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "docker/codex-agent/render_codex_config.mjs"
    completed = subprocess.run(
        ["node", str(script), str(output), str(servers)],
        check=True,
        capture_output=True,
        text=True,
        # A deliberately small environment proves the renderer reads only its
        # own inputs. Windows still needs the OS variables Node requires to
        # start at all, so those are carried over explicitly.
        env=_renderer_env(
            EVAL_MODEL="deepseek/deepseek-v4-flash",
            EVAL_REASONING_EFFORT="high",
            EVAL_GATEWAY_URL="http://llm-gateway:8080/v1",
            EVAL_WIRE_API="responses",
        ),
    )
    assert completed.returncode == 0
    rendered = output.read_text(encoding="utf-8")
    assert '[mcp_servers.blender]' in rendered
    assert 'command = "/opt/blender-mcp/bin/blender-mcp"' in rendered
    assert '[mcp_servers.unreal-mcp]' in rendered
    assert 'url = "http://host.docker.internal:8000/mcp"' in rendered


def test_dsh_projection_uses_standard_acp_mcp_shape() -> None:
    projected = project_dsh_mcp_servers(
        {
            "unreal-mcp": {
                "transport": "streamable-http",
                "url": "http://host.docker.internal:8000/mcp",
                "tool_timeout_sec": 12,
            },
            "comfyui": {
                "transport": "stdio",
                "command": "/opt/comfy-mcp/bin/comfy-mcp",
                "args": ["--stdio"],
                "env": {"COMFYUI_URL": "http://host.docker.internal:8188"},
            },
        }
    )

    assert projected[0]["type"] == "http"
    assert projected[0]["name"] == "unreal-mcp"
    assert projected[0]["url"] == "http://host.docker.internal:8000/mcp"
    assert projected[1]["name"] == "comfyui"
    assert projected[1]["args"] == ["--stdio"]
    assert projected[1]["env"] == [
        {"name": "COMFYUI_URL", "value": "http://host.docker.internal:8188"}
    ]
