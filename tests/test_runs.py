"""Tests for resolved run configuration and repository snapshots.

Every fixture builds the real directory layout -- profiles under `profiles/`,
Tasks under `tasks/` -- because that is the only shape the resolver accepts.
An earlier version of these tests wrote inline definitions into the config file,
which the resolver no longer reads.
"""

from pathlib import Path

from ai_native_evals.runs.lifecycle import load_manifest, prepare_run
from ai_native_evals.runs.resolver import resolve_run


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, *, with_sandbox: bool = False) -> Path:
    """A minimal repository the resolver accepts."""
    _write(
        tmp_path / "config" / "eval.yaml",
        """
version: 1
task_roots:
- tasks
profile_roots:
  agents: profiles/agents
  models: profiles/models
  mcp: profiles/mcp
  sandboxes: profiles/sandboxes
  presets: config/presets
paths:
  runs_root: EvalRuns
defaults:
  agent: codex
  model_profile: test-model
  mcp_profile: test-mcp
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
        tmp_path / "profiles" / "mcp" / "test-mcp.yaml",
        "id: test-mcp\nservers:\n  blender:\n    transport: stdio\n    command: blender-mcp\n",
    )
    if with_sandbox:
        _write(
            tmp_path / "profiles" / "sandboxes" / "test-sandbox.yaml",
            "id: test-sandbox\nread_only_root: true\n",
        )
    _write(
        tmp_path / "tasks" / "task-001" / "task.yaml",
        """
id: task-001
version: 1
prompt_file: prompt.md
resources: []
""",
    )
    _write(tmp_path / "tasks" / "task-001" / "prompt.md", "Do the thing.")
    return tmp_path


def test_resolve_run_uses_defaults(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    spec = resolve_run(root, "task-001")

    assert spec.agent == "codex"
    assert spec.model == "test/model"
    assert spec.protocol == "responses"
    assert spec.run_dir.parent == (root / "EvalRuns").resolve()


def test_a_task_must_be_a_bundle(tmp_path: Path) -> None:
    """There is no inline task definition to fall back to."""
    root = _repo(tmp_path)

    try:
        resolve_run(root, "does-not-exist")
    except Exception as exc:  # noqa: BLE001 - the message is what matters
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("an unknown task must be refused")


def test_prepare_run_writes_a_workspace_and_manifest(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    spec = resolve_run(root, "task-001")
    run_dir = prepare_run(root, spec)
    manifest = load_manifest(run_dir)

    assert manifest["status"] == "prepared"
    assert (run_dir / "workspace" / "output").is_dir()
    assert (run_dir / "workspace" / "scratch").is_dir()
    assert manifest["paths"]["workspace"] == str(run_dir / "workspace")


def test_a_run_records_the_resolved_agent_profile(tmp_path: Path) -> None:
    """The manifest carries the profile, and the runtime refuses without it."""
    root = _repo(tmp_path)

    spec = resolve_run(root, "task-001")
    manifest = load_manifest(prepare_run(root, spec))

    profile = manifest["run"]["agent_profile"]
    assert profile["id"] == "codex"
    assert profile["image"] == "test-codex"


def test_explicit_selectors_override_the_defaults(tmp_path: Path) -> None:
    root = _repo(tmp_path, with_sandbox=True)
    _write(
        root / "profiles" / "mcp" / "other.yaml",
        "id: other\nservers: {}\n",
    )

    spec = resolve_run(root, "task-001", mcp_profile="other", sandbox_profile="test-sandbox")

    assert spec.mcp_profile == "other"
    assert spec.sandbox_profile == "test-sandbox"


def test_a_preset_supplies_a_whole_configuration(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(
        root / "config" / "presets" / "preset.yaml",
        "id: preset\nagent: codex\nmodel_profile: test-model\nmcp_profile: test-mcp\n",
    )

    spec = resolve_run(root, "task-001", preset="preset")

    assert spec.agent == "codex"
    assert spec.model_profile == "test-model"
    assert spec.mcp_profile == "test-mcp"


def test_a_task_declared_execution_reaches_the_spec(tmp_path: Path) -> None:
    """A Task states what it needs; the resolver honours it."""
    root = _repo(tmp_path)
    _write(
        root / "profiles" / "mcp" / "blender-host.yaml",
        "id: blender-host\nservers:\n  blender:\n    transport: stdio\n    command: b\n",
    )
    _write(
        root / "tasks" / "task-001" / "task.yaml",
        """
id: task-001
version: 1
prompt_file: prompt.md
resources: []
execution:
  mcp_profile: blender-host
""",
    )

    spec = resolve_run(root, "task-001")

    assert spec.mcp_profile == "blender-host"


def test_an_unknown_agent_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    try:
        resolve_run(root, "task-001", agent="nope")
    except Exception as exc:  # noqa: BLE001 - the message is what matters
        assert "nope" in str(exc)
    else:
        raise AssertionError("an unknown agent must be refused")
