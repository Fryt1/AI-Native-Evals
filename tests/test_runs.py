"""Tests for resolved run configuration and repository snapshots."""

from pathlib import Path

from ai_native_evals.runs.lifecycle import load_manifest, prepare_run
from ai_native_evals.runs.resolver import resolve_run


def _config(root: Path) -> Path:
    config = root / "config.yaml"
    config.write_text(
        """
paths:
  game_engine: game-engine
  dsh: dsh
  runs_root: EvalRuns
defaults:
  agent: codex
  model_profile: deepseek
  mcp_profile: blender
model_profiles:
  deepseek:
    model: deepseek/deepseek-v4-flash
    protocol: responses
agents:
  codex:
    image: test-codex
mcp_profiles:
  blender:
    blender: true
""",
        encoding="utf-8",
    )
    return config


def test_resolve_run_uses_defaults_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "game-engine").mkdir()
    (tmp_path / "dsh").mkdir()
    spec = resolve_run(
        tmp_path,
        "task-001",
        config_path=_config(tmp_path),
        agent="codex",
        game_engine_ref="main",
    )

    assert spec.agent == "codex"
    assert spec.model == "deepseek/deepseek-v4-flash"
    assert spec.protocol == "responses"
    assert spec.game_engine_root == (tmp_path / "game-engine").resolve()
    assert spec.run_dir.parent == (tmp_path / "EvalRuns").resolve()


def test_prepare_run_writes_snapshot_and_manifest(tmp_path: Path) -> None:
    engine = tmp_path / "game-engine"
    dsh = tmp_path / "dsh"
    engine.mkdir()
    dsh.mkdir()
    (engine / "AGENTS.md").write_text("rules", encoding="utf-8")
    (engine / ".venv").mkdir()
    (engine / ".venv" / "ignored.txt").write_text("ignored", encoding="utf-8")

    spec = resolve_run(tmp_path, "task-001", config_path=_config(tmp_path))
    run_dir = prepare_run(tmp_path, spec)
    manifest = load_manifest(run_dir)

    assert manifest["status"] == "prepared"
    assert (run_dir / "workspace" / "game-engine" / "AGENTS.md").read_text() == "rules"
    assert not (run_dir / "workspace" / "game-engine" / ".venv").exists()
    assert (run_dir / "workspace" / "output").is_dir()
    assert (run_dir / "workspace" / "scratch").is_dir()
    assert manifest["paths"]["project"] == str(run_dir / "workspace" / "game-engine")
    assert manifest["paths"]["workspace"] == str(run_dir / "workspace")
    assert manifest["snapshots"]["game_engine"]["dirty"] is False
