"""Tests for the declarative per-run MCP profile contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_native_evals.mcp import project_dsh_mcp_servers
from ai_native_evals.runs.resolver import resolve_run


def test_resolve_run_substitutes_mcp_profile_variables(tmp_path: Path) -> None:
    (tmp_path / "game-engine").mkdir()
    (tmp_path / "dsh").mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  game_engine: game-engine
  dsh: dsh
  runs_root: EvalRuns
defaults:
  agent: codex
  model_profile: deepseek
  mcp_profile: ue5
model_profiles:
  deepseek:
    model: deepseek/deepseek-v4-flash
agents:
  codex:
    image: test-codex
mcp_profiles:
  ue5:
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
        encoding="utf-8",
    )

    spec = resolve_run(tmp_path, "test", config_path=config)

    assert spec.mcp_ue5 is True
    assert spec.mcp_blender is False
    assert spec.mcp_servers["unreal-mcp"]["url"] == "http://host.docker.internal:8000/mcp"
    assert spec.mcp_servers["extra"]["env"]["TARGET"] == "host.docker.internal"


def test_prepare_run_persists_resolved_mcp_servers(tmp_path: Path) -> None:
    (tmp_path / "game-engine").mkdir()
    (tmp_path / "dsh").mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  game_engine: game-engine
  dsh: dsh
  runs_root: EvalRuns
defaults:
  agent: codex
  model_profile: deepseek
  mcp_profile: none
model_profiles:
  deepseek:
    model: deepseek/deepseek-v4-flash
agents:
  codex:
    image: test-codex
mcp_profiles:
  none:
    servers: {}
""",
        encoding="utf-8",
    )
    spec = resolve_run(tmp_path, "test", config_path=config)
    from ai_native_evals.runs.lifecycle import load_manifest, prepare_run

    run_dir = prepare_run(tmp_path, spec)
    manifest = load_manifest(run_dir)
    mcp_path = Path(manifest["paths"]["mcp_servers"])
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == {}


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
        env={
            "PATH": __import__("os").environ["PATH"],
            "EVAL_MODEL": "deepseek/deepseek-v4-flash",
            "EVAL_REASONING_EFFORT": "high",
            "EVAL_GATEWAY_URL": "http://llm-gateway:8080/v1",
            "EVAL_WIRE_API": "responses",
        },
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

    assert projected[0]["transport"] == "streamable-http"
    assert projected[0]["serverName"] == "unreal-mcp"
    assert projected[0]["toolCallTimeoutMs"] == 12_000
    assert projected[1]["transport"] == "stdio"
    assert projected[1]["args"] == ["--stdio"]
